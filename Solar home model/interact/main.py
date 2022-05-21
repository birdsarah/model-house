##### WIIIPPPPPP

import bokeh.plotting as bkpO
import bokeh.io as bkio
import bokeh.models as bkm
import bokeh.palettes

battery_capacity = 13.5 # kWh (Tesla Powerwall is 13.5 kWh)
pv = 9 # kWh (18kWh LG system ~$40k; 9kWh LG system ~$22k)
energy_consumption_per_house = 18000 # kWh
tilt = 30 # [20, 30, 45]
azimuth = 210 # [120, 210, 300]

summary = make_summary(tilt, azimuth, battery_capacity, pv, energy_consumption_per_house)
source = bkm.ColumnDataSource(summary)

columns = [
        bkm.TableColumn(field="quantity", title="Quantity"),
        bkm.TableColumn(field="value", title="Value"),
    ]
data_table = bkm.DataTable(source=source, columns=columns, width=400, height=280)

def update(tilt, azimuth, battery_capacity, pv, energy_consumption_per_house):
    summary = make_summary(tilt, azimuth, battery_capacity, pv, energy_consumption_per_house)
    data_table.source.data['quantity'] = summary.quantity
    data_table.source.data['values'] = summary.values
