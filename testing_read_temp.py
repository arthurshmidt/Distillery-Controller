# Testing file fof the PI-SPI-DIN-AI Analog Input Module that communicates to
# Raspberry Pi via the SPI bus.
from time import sleep
from widgetlords.pi_spi_din import *
from widgetlords import *

init()
inputs = Mod8AI(ChipEnable.CE0)

def celcius_to_fahrnheit(temp_c):
    temp_f = (temp_c * (9/5)) + 32

    return temp_f

def read_basic():
    while True:
        print(inputs.read_single(0))
        sleep(0.5)

def read_temperature():
    while True:
        A1 = inputs.read_single(0)         # A1 - terminal block
        temp_c = steinhart_hart(10000, 3380, 4095, A1)
        print("Temperature in Celcius: {}".format(temp_c))
        print("Temperature in Fahrenheit: {}".format(celcius_to_fahrnheit(temp_c)))
        sleep(0.5)

read_temperature()
