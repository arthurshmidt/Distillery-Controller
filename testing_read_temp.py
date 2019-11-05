# Testing file fof the PI-SPI-DIN-AI Analog Input Module that communicates to
# Raspberry Pi via the SPI bus.
from time import sleep
from widgetlords.pi_spi_din import *
from widgetlords import *
from os import system

init()
inputs = Mod8AI(ChipEnable.CE0)

def clear_screen():
    _ = system("clear")

def celcius_to_fahrnheit(temp_c):
    temp_f = (temp_c * (9/5)) + 32

    return temp_f

def read_basic():
    while True:
        print(inputs.read_single(0))
        sleep(0.5)

while True:
    clear_screen()
    d_return = inputs.read_single(0)         # Dephleg Return
    c_return = inputs.read_single(1)         # Condensor Return
    d_supply = inputs.read_single(2)         # Dephleg Supply
    c_supply = inputs.read_single(3)         # Condensor Supply 
        
    d_return_c = steinhart_hart(10000, 3380, 4095, d_return) - 3.0
    c_return_c = steinhart_hart(10000, 3380, 4095, c_return) - 3.0
    d_supply_c = steinhart_hart(10000, 3380, 4095, d_supply) - 3.0
    c_supply_c = steinhart_hart(10000, 3380, 4095, c_supply) - 3.0

    print("Deph Supply: {0:.2f}F".format(celcius_to_fahrnheit(d_supply_c)))
    print("Deph Return: {0:.2f}F".format(celcius_to_fahrnheit(d_return_c)))
    print("Cond Supply: {0:.2f}F".format(celcius_to_fahrnheit(c_supply_c)))
    print("Cond Return: {0:.2f}F".format(celcius_to_fahrnheit(c_return_c)))
    sleep(2)

