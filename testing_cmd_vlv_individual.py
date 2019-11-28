# Testing file fof the PI-SPI-DIN-AO Analog Output Module that communicates to
# Raspberry Pi via the SPI bus.
from time import sleep
from widgetlords.pi_spi import *
# from widgetlords import *

init()
outputs = Mod2AO()

def percent_to_da(valve_percent):
    da_signal = ((4000-800)/(100-0)) * valve_percent + 800
    return da_signal

# Main program loop
while True:
    pin_out = int(input("Enter pin-out (0-3): "))
    valve_percent = int(input("Enter valve position in %: "))
    da_signal = int(percent_to_da(valve_percent))

    print("You entered {}% on pin-out {} = {}da".format(valve_percent,pin_out,da_signal))
    outputs.write_single(pin_out,da_signal)     # 800 = 4ma ; 4000 = 20ma
    
    _ = input("Press Enter to Continue.")
#    sleep(2)
