from time import sleep
from widgetlords.pi_spi_din import *

init()
outputs = Mod4AO()

while True:
    outputs.write_single(0,800)
    outputs.write_single(1,800)
    _ = input("Entered a DA of 800 on Pin-out 0,1")
    outputs.write_single(0,4000)
    outputs.write_single(1,4000)
    _ = input("entered a DA of 4000 on Pin-out 0,1")

