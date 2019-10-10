# Testing file fof the PI-SPI-DIN-AO Analog Output Module that communicates to
# Raspberry Pi via the SPI bus.
from time import sleep
from widgetlords.pi_spi_din import *
# from widgetlords import *

init()
outputs = Mod4AO()

while True:
    print("Commanding Valve to 4ma")
    outputs.write_single(0,800)     # 800 = 4ma
    sleep(2)

    print("Commanding Valve to 20ma")
    outputs.write_single(0,4000)    # 4000 = 20ma
    sleep(2)
