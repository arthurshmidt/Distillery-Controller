# PID control loop for a vodka setup on the still.
# Need to command the PI -> AI and AO functions to control valve outputs
# based on discharge temperture from the deflagmator and condensing column
from time import sleep
from widgetlords.pi_spi_din import *    # for AI module
# from widgetlords.pi_spi import *        # for AO module
from widgetlords import *               # for AI module
from simple_pid import PID              # PID control library for actuation
from os import system                   # Needed for clearing the screen

init()                                  # required for controller
thermister_inputs = Mod8AI(ChipEnable.CE0)  # AI board designation
valve_outputs = Mod4AO()                    # AO board designation

# Global variables
dephlegmator_temp_st = 150              # setup dephlegmator to  150F
condensor_temp_st = 150                # set condensor to 150F
therm_calibration_factor = 3.0          # Calibration for thermisters (Celcius)

# Board addresses for controllers
dephlegmator_vlv_output = 0
condensor_vlv_output = 1
dephlegmator_therm_return_input = 0
dephlegmator_therm_supply_input = 2
condensor_therm_return_input = 1
condensor_therm_supply_input = 3

# PID value setting for tunning control
deph_kvalue_proportional = 1
deph_kvalue_integral = 0.1
deph_kvalue_derivative = 0.05
cond_kvalue_proportional = 1
cond_kvalue_integral = 0.1
cond_kvalue_derivative = 0.05

# function: clear screen
def clear_screen():
    _ = system("clear")

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
def command_valves(dephlegmator_vlv_percent_cmd,condensor_vlv_percent_cmd):
    # Command dephlegmator
    dephlegmator_vlv_da_cmd = percent_to_da(dephlegmator_vlv_percent_cmd)
    valve_outputs.write_single(dephlegmator_vlv_output,dephlegmator_vlv_da_cmd)

    # Command condensor
    condensor_vlv_da_cmd = percent_to_da(condensor_vlv_percent_cmd)
    valve_outputs.write_single(condensor_vlv_output,condensor_vlv_da_cmd)

# function: testing thermocouple reading
# input: (tuple) temperatures, from the read_temperatures()
# output: Display temperaure readings
def test_temperature(temperatures_f):
    print("Deph Supply: {0:.2f}F".format(temperatures_f[0]))
    print("Deph Return: {0:.2f}F".format(temperatures_f[1]))
    print("Cond Supply: {0:.2f}F".format(temperatures_f[2]))
    print("Cond Return: {0:.2f}F".format(temperatures_f[3]))

# function: testing valve commands
# input: none
# output: Display valve command.  Signal output to valves.
def test_valves():
    valve_outputs.write_single(0,800)
    valve_outputs.write_single(1,800)
    _ = input("Entered a DA of 800 on Pin-out 0,1")
    valve_outputs.write_single(0,4000)
    valve_outputs.write_single(1,4000)
    _ = input("Entered a DA of 4000 on Pin-out 0,1")

# function: testing valve commands individually
# input: none
# output: Display valve command.  Signal output to valves.
def test_valves_individual():
    pin_out = int(input("Enter pin-out (0-3): "))
    valve_percent = int(input("Enter valve position in %: "))
    da_signal = int(percent_to_da(valve_percent))

    print("You entered {}% on pin-out {} = {}da".format(valve_percent,pin_out,da_signal))
    valve_outputs.write_single(pin_out,da_signal)     # 800 = 4ma ; 4000 = 20ma

    _ = input("Press Enter to Continue.")

# ************************************************************************* #
#                                                                           #
#                                   Main Code                               #
#                                                                           #
# ************************************************************************* #

# Define PID objects
# PID for dephlegmator
dephlegmator_pid = PID(deph_kvalue_proportional,deph_kvalue_integral,deph_kvalue_derivative,dephlegmator_temp_st)
dephlegmator_pid.sample_time = 5
dephlegmator_pid.output_limits = (0, 100)

# PID for condensor
# condensor_pid = PID(cond_kvalue_proportional,cond_kvalue_integral,cond_kvalue_derivative,condensor_temp_st)
# condensor_pid.sample_time = 5
# condensor_pid.output_limits = (0, 100)

# Main loop
while True:
    # Basic Testing Functions - Uncomment to activate
    # test_temperature(read_temperatures())
    # test_valves()
    # test_valves_individual()

    # read temperatures and create readalable variables
    temperatures_f = read_temperatures()
    dephlegmator_temp_supply_f = temperatures_f[0]
    dephlegmator_temp_return_f = temperatures_f[1]
    condensor_temp_supply_f = temperatures_f[2]
    condensor_temp_return_f = temperatures_f[3]

    # PID control - Uncomment to activate PID
    dephlegmator_valve_percent_cmd = dephlegmator_pid(dephlegmator_temp_return_f)
    # condensor_valve_percent_cmd = condensor_pid(condensor_temp_return_f)

    # Command the valves
    # command_valves(dephlegmator_vlv_percent_cmd,condensor_vlv_percent_cmd)
    # Testing - Command dephlegmator
    dephlegmator_vlv_da_cmd = percent_to_da(dephlegmator_vlv_percent_cmd)
    valve_outputs.write_single(dephlegmator_vlv_output,dephlegmator_vlv_da_cmd)

    # Testing - Display values to be tested
    print("Temp St: {}".format(dephlegmator_temp_st))
    print("Temp Read: {0:.2f}F".format(dephlegmator_temp_return_f))
    print("VLV PID: {}".format(dephlegmator_vlv_da_cmd))

    # Insert a delay & clear
    sleep(2)
    clear_screen()
