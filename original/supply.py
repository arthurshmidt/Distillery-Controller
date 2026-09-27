# PID Control Loop for a supply temperature controller to the still.
# Need to cammand the PI -> AI and AO function to control valve outputs
# based on suppy temperature to the deflagmator.

# ************************************************************************* #
#                                                                           #
#                       Modules and Classes                                 #
#                                                                           #
# ************************************************************************* #

# system Modules
from os import system           # Needed for clearing the screen
from time import sleep

# Controller Modules
from widgetlords.pi_spi_din import *    # for AI & AO Module
from widgetlords import *              # for AI module
from simple_pid import PID       # PID control library for actuation

# ************************************************************************* #
#                                                                           #
#                  Initializations & Gloabal Variables                      #
#                                                                           #
# ************************************************************************* #

# Initializations
init()                                      # required for controller
thermister_inputs = Mod8AI(ChipEnable.CE0)  # AI Board designation
valve_outputs = Mod4AO()                    # AO Board designation

# Global Variables
supply_temp_st = 90
therm_calibration_factor = 3.0

# Board addresses for controllers
dephlegmator_vlv_output = 0
condensor_vlv_output = 1
supply_vlv_output = 2
dephlegmator_therm_return_input = 0
dephlegmator_therm_supply_input = 2
condensor_therm_return_input = 1
condensor_therm_supply_input = 3

# PID value setting for tunning control
sup_kvalue_proportional = -1
sup_kvalue_integral = -0.01
sup_kvalue_derivative = 0.0

# ************************************************************************* #
#                                                                           #
#                             Function Definitions                          #
#                                                                           #
# ************************************************************************* #

# function: clear screen
def clear_screen():
    _ = system("clear")

# function: testing valve commands
# input: none
# output: Display valve command.  Signal output to valves.
def test_valve():
    valve_outputs.write_single(supply_vlv_output,800)
    _ = input("Entered a DA of 800 on Pin-out 2")
    valve_outputs.write_single(supply_vlv_output,4000)
    _ = input("Entered a DA of 4000 on Pin-out 2")

# function: for celcius to Fahrenheit converstion for controller
def celcius_to_fahrnheit(temp_c):
    temp_f = (temp_c * (9/5)) + 32

    return temp_f

# funtion: for valve outputs (percent to da output)
# note: valve controller is based on a DA signal and not Percent.
def percent_to_da(valve_percent):
    da_signal = ((4000-800)/(100-0)) * valve_percent + 800

    return da_signal

# function: read temperatures in and do the necessary conversions
# output: (tuple) - temperatures (dephlegmator supply, dephlegmator return,
#                                 condensor supply, condensor return)
def read_temperatures():
    # read in temperatures !!! I believe values are in DA
    dephlegmator_temp_return_da = thermister_inputs.read_single(dephlegmator_therm_return_input)
    dephlegmator_temp_supply_da = thermister_inputs.read_single(dephlegmator_therm_supply_input)
    condensor_temp_supply_da = thermister_inputs.read_single(condensor_therm_supply_input)
    condensor_temp_return_da = thermister_inputs.read_single(condensor_therm_return_input)

    # convert temperatures using Steinhard equation to get Celcious
    dephlegmator_temp_supply_c = steinhart_hart(10000,3380,4095,dephlegmator_temp_supply_da) - therm_calibration_factor
    dephlegmator_temp_return_c = steinhart_hart(10000,3380,4095,dephlegmator_temp_return_da) - therm_calibration_factor
    condensor_temp_supply_c = steinhart_hart(10000,3380,4095,condensor_temp_supply_da) - therm_calibration_factor
    condensor_temp_return_c = steinhart_hart(10000,3380,4095,condensor_temp_return_da) - therm_calibration_factor

    # convert temperatures from Celcius to Fahrenheit
    dephlegmator_temp_supply_f = celcius_to_fahrnheit(dephlegmator_temp_supply_c)
    dephlegmator_temp_return_f = celcius_to_fahrnheit(dephlegmator_temp_return_c)
    condensor_temp_supply_f = celcius_to_fahrnheit(condensor_temp_supply_c)
    condensor_temp_return_f = celcius_to_fahrnheit(condensor_temp_return_c)

    return dephlegmator_temp_supply_f, dephlegmator_temp_return_f,condensor_temp_supply_f, condensor_temp_return_f

# function: command valves to the desired valve position
# input: dephlegmator valve command in %, condensor valve command in %
# output: none
def command_valves(supply_vlv_percent_cmd):
    # Command dephlegmator
    supply_vlv_da_cmd = int(percent_to_da(supply_vlv_percent_cmd))
    valve_outputs.write_single(supply_vlv_output,supply_vlv_da_cmd)

# ************************************************************************* #
#                                                                           #
#                                   Main Code                               #
#                                                                           #
# ************************************************************************* #

# Define PID objects
# PID for supply
supply_pid = PID(sup_kvalue_proportional,sup_kvalue_integral,sup_kvalue_derivative,supply_temp_st)
supply_pid.sample_time = 1
supply_pid.output_limits = (0, 60)

# Command System to inital positions
valve_outputs.write_single(supply_vlv_output,4000)
print("Valves commanded fully open.")
_ = input("Press Enter to continue")
valve_outputs.write_single(supply_vlv_output,800)
print("Valves commanded fully closed.")
_ = input("Press Enter to continue")

while True:
    # Test Valve
    # test_valve()

    # read temperatures and create readalable variables
    temperatures_f = read_temperatures()
    dephlegmator_temp_supply_f = temperatures_f[0]
    dephlegmator_temp_return_f = temperatures_f[1]
    condensor_temp_supply_f = temperatures_f[2]
    condensor_temp_return_f = temperatures_f[3]

    # PID control - Uncomment to activate PID
    supply_vlv_percent_cmd = supply_pid(dephlegmator_temp_supply_f)

    # Command the valves
    command_valves(supply_vlv_percent_cmd)
    p, i, d = supply_pid.components

    # Testing - Display values to be tested
    print("Temp St: {}".format(supply_temp_st))
    print("Temp Read: {0:.2f}F".format(dephlegmator_temp_supply_f))
    print("VLV PID: {}".format(supply_vlv_percent_cmd))
    print("PID - P: {}, I: {}, D: {}".format(p,i,d))

    # Insert a delay & clear
    sleep(2)
    clear_screen()
