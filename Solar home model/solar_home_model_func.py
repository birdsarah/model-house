import pandas as pd
from datetime import datetime

# PV

def read_pvwatts(tilt, azimuth):
    # Read NREL generation data (PVWatts) for particular set-up
    # For Caswell house, set up with highest monthly average production across the year is:
    # tilt 30, azimuth 210

    # The data is hourly

    pvwatts_file = f'pvwatts_hourly 10 kW az {azimuth} tilt {tilt}.csv'

    pvwatts_header = pd.read_csv(
        pvwatts_file,
        header = 0,
        nrows = 13,
        names = ['category', 'value'],
    )
    pvwatts_header = pvwatts_header.set_index('category')['value']
    
    # TEST: check that tilt & azimuth are as expected
    if int(pvwatts_header.at['Array Tilt (deg):']) != tilt:
        print("Error!")
    if int(pvwatts_header.at['Array Azimuth (deg):']) != azimuth:
        print("Error!")
    # END OF TEST

    pvwatts = pd.read_csv(
        pvwatts_file,
        header = 14,
    )

    # remove totals
    pvwatts = pvwatts[pvwatts['Month']!='Totals']
    
    # set dtypes
    for col in ['Month', 'Day', 'Hour']:
        pvwatts[col] = pvwatts[col].astype(int)
        
    # rename columns
    pvwatts = pvwatts.rename(columns={
        'DC Array Output (W)': 'DC Array Output (Wh)',
        'AC System Output (W)': 'AC System Output (Wh)',
    })
    
    return pvwatts, pvwatts_header

def pvwatts_set_datetime_index(pvwatts):
    datetime = '2018-' 
    datetime += pvwatts['Month'].astype(str).str.zfill(2) + '-'
    datetime += pvwatts['Day'].astype(str).str.zfill(2) + ' '
    datetime += pvwatts['Hour'].astype(str).str.zfill(2) + ':00:00'
    datetime = pd.to_datetime(datetime)
    pvwatts['Datetime'] = datetime
    pvwatts = pvwatts.set_index('Datetime')
    
    return pvwatts

def scale_pvwatts(pvwatts, pv_kwh):
    # default size is 10 kWh
    scaling_factor = pv_kwh / 10
    # print(f"scaling_factor: {scaling_factor}")
    
    for col in ['DC Array Output (Wh)', 'AC System Output (Wh)']:
        pvwatts[col] = pvwatts[col] * scaling_factor
    
    return pvwatts

def get_pvwatts(tilt, azimuth, pv_system_size):
    pvwatts, _ = read_pvwatts(tilt, azimuth)
    pvwatts = pvwatts_set_datetime_index(pvwatts)
    return scale_pvwatts(pvwatts, pv_system_size)

# ResStock

def resstock_read():
    # Read ResStock data for Travis County
    # Note: should the code have an extra zero? g48004530
    resstock_file = 'g4804530-single-family_detached.csv'
    resstock = pd.read_csv(resstock_file)

    resstock['timestamp'] = pd.to_datetime(resstock['timestamp'])
    resstock = resstock.set_index('timestamp')
    
    return resstock

def resstock_downsample_to_hour(resstock):
    df = resstock.copy()
    cols_to_drop = [
        'in.county', 'in.geometry_building_type_recs',
        'models_used', 'units_represented'
    ]
    df = df.drop(cols_to_drop, axis=1)
    df = df.resample(rule='H').sum()

    resstock_hr = df

    # TEST: check total is same after resample:
    total_original = resstock['out.site_energy.total.energy_consumption'].sum()
    total_resample = resstock_hr['out.site_energy.total.energy_consumption'].sum()
    if abs((total_original - total_resample)/total_original) > 1e-6:
        print('Error!' + f"original: {total_original}; resample: {total_resample}")
        
    return resstock_hr

def resstock_hr_norm_guesstimate(resstock, energy_consumption_per_house):
    number_of_houses_guesstimate = int(resstock['out.site_energy.total.energy_consumption'].sum() / energy_consumption_per_house)
    resstock_hr = resstock_downsample_to_hour(resstock)
    return resstock_hr / number_of_houses_guesstimate


# Utils

def create_merged(pvwatts, resstock_hr_norm):
    'Total energy consumption (kWh)'
    resstock_hr_norm = resstock_hr_norm.rename(columns={
        'out.site_energy.total.energy_consumption': 'Total energy consumption (kWh)',
    })
    
    pvwatts_merge = pvwatts.copy()
    pvwatts_merge['AC System Output (kWh)'] = pvwatts_merge[['AC System Output (Wh)']] / 1e3
    pvwatts_merge = pvwatts_merge.rename(columns={'AC System Output (kWh)': 'PV generation AC (kWh)'})

    merged = pd.concat([
        resstock_hr_norm,
        pvwatts_merge[['PV generation AC (kWh)']],
    ], axis=1)

    # fill in missing value for PV in 2019-01-01 00:00:00 -- nighttime, so no generation
    pvwatts_nan_len = len(merged[merged['PV generation AC (kWh)'].isna()])
    if pvwatts_nan_len==1:
        merged['PV generation AC (kWh)'] = merged['PV generation AC (kWh)'].fillna(0)
    else:
        print("Error!" + f" There was only expected to be 1 nan row, but there were {pvwatts_nan_len} rows")
        
    return merged


def calculate_shortfall_excess(merged):
    diff = merged['PV generation AC (kWh)'] - merged['Total energy consumption (kWh)']

    shortfall_pv = -1 * diff[diff <= 0]
    shortfall_pv.name = 'PV shortfall kWh'

    excess_pv = diff[diff > 0]
    excess_pv.name = 'PV excess kWh'

    merged = pd.concat([
        merged,
        shortfall_pv,
        excess_pv,
    ], axis=1).fillna(0)

    merged = merged.reset_index()
    
    return merged


def battery_charge_discharge(merged, battery_capacity_kwh):
    # model battery storage
    # set a certain capacity of battery
    # then step through the time series, to fill the battery when there is excess (up to its limit)
    # and to draw down the battery (only to zero)

    merged['battery kWh'] = float(0) # initialize

    for row in merged.index:
        
        if row == 0:
            battery = float(0)
        else:
            # get value from previous row
            battery = merged.at[row-1, 'battery kWh']

        battery_capacity_kwh = float(battery_capacity_kwh)
        battery_spare_cap = battery_capacity_kwh - battery

        pv_excess = merged.at[row, 'PV excess kWh']
        pv_shortfall = merged.at[row, 'PV shortfall kWh']

        # charge the battery
        # print(f"pv_excess: {pv_excess} & battery_spare_cap: {battery_spare_cap}")
        charged = min(pv_excess, battery_spare_cap)
        merged.at[row, 'charged kWh'] = charged

        battery += charged

        # sold to grid
        sold = pv_excess - charged
        

        # discharge battery
        discharged = min(pv_shortfall, battery)

        battery += -1 * discharged
        merged.at[row, 'discharged kWh'] = discharged
        
        # total shortfall
        total_consump = merged.at[row, 'Total energy consumption (kWh)']
        pv_gen = merged.at[row, 'PV generation AC (kWh)']
        total_supply = pv_gen + discharged
        
        total_shortfall = max(total_consump - total_supply, 0)

        # write values for battery at end of time period
        merged.at[row, 'battery spare kWh'] = battery_spare_cap
        merged.at[row, 'battery kWh'] = battery
        
        merged.at[row, 'bought kWh'] = total_shortfall       
        merged.at[row, 'sold kWh'] = sold

    return merged


def build_results(pv_model, house_model, battery_capacity):
    model = create_merged(pv_model, house_model)
    model = calculate_shortfall_excess(model)
    model = battery_charge_discharge(model, battery_capacity)
    model = model.set_index('index')
    model['net bought kWh'] = model['bought kWh'] - model['sold kWh']
    return model