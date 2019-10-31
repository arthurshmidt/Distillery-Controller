# Testing file fof the PI-SPI-DIN-AO Analog Output Module that communicates to
# Raspberry Pi via the SPI bus.
from time import sleep
from widgetlords.pi_spi_din import *
# from widgetlords import *

init()
outputs = Mod4AO()

def percent_to_da(valve_percent):
    da_signal = ((4000-800)/(100-0)) * valve_percent + 800
    return da_signal

# Main program loop
while True:
    valve_percent = int(input("Enter valve position in %: "))
    da_signal = percent_to_da(valve_percent)

    print("You entered {}% = {}da".format(valve_percent,da_signal))
    outputs.write_single(0,da_signal)     # 800 = 4ma ; 4000 = 20ma
    
    _ = input("Press Enter to Continue.")
#    sleep(2)
