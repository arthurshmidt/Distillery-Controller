# PID control loop for a vodka setup on the still.
# Need to command the PI -> AI and AO functions to control valve outputs
# based on discharge temperture from the deflagmator and condensing column
from time import sleep
from widgetlords.pi_spi_din import *    # for AI module
from widgetlords.pi_spi import *        # for AO module
from widgetlords import *               # for AI module
from simple_pid import PID              # PID control library for actuation

init()                                  # required for controller
thermister_inputs = Mod8AI(ChipEnable.CE0)  # AI board designation
valve_outputs = Mod4AO()                    # AO board designation

# Global variables
dephlegmator_temp_st = 150              # setup dephlegmator to  150F
condensor_temp_st = 150                # set condensor to 150F

# Board addresses for controllers
dephlegmator_vlv_output = 0
condensor_vlv_output = 1
dephlegmator_therm_return_input = 0
dephlegmator_therm_supply_input = 1
condensor_therm_return_input = 2
condensor_therm_supply_input = 3

# PID value setting for tunning control
dephlegmator_kvalue_proportional = 1
dephlegmator_kvalue_integral = 0.1
dephlegmator_kvalue_derivative = 0.05
condensor_kvalue_proportional = 1
condensor_kvalue_integral = 0.1
condensor_kvalue_derivative = 0.05

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
    dephlegmator_temp_supply_c = steinhart_hart(10000,3380,4095,dephlegmator_temp_supply_da)
    dephlegmator_temp_return_c = steinhart_hart(10000,3380,4095,dephlegmator_temp_return_da)
    condensor_temp_supply_c = steinhart_hart(10000,3380,4095,condensor_temp_supply_da)
    condensor_temp_return_c = steinhart_hart(10000,3380,4095,condensor_temp_return_da)

    # convert temperatures from Celcius to Fahrenheit
    dephlegmator_temp_supply_f = celcius_to_fahrnheit(dephlegmator_temp_supply_c)
    dephlegmator_temp_return_f = celcius_to_fahrnheit(dephlegmator_temp_return_c)
    condensor_temp_supply_f = celcius_to_fahrnheit(condensor_temp_supply_c)
    condensor_temp_return_f = celcius_to_fahrnheit(condensor_temp_return_c)

    return dephlegmator_temp_supply_f, dephlegmator_temp_return_f,
           condensor_temp_supply_f, condensor_temp_return_f

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

# ************************************************************************* #
#                                                                           #
#                                   Main Code                               #
#                                                                           #
# ************************************************************************* #

# Define PID objects
dephlegmator_pid = PID(dephlegmator_kvalue_proportional,dephlegmator_kvalue_integral,
                       dephlegmator_kvalue_derivative,dephlegmator_temp_st)
dephlegmator_pid.sample_time = 1
dephlegmator_pid.output_limits = (0, 100)
condensor_pid = PID(condensor_kvalue_proportional,condensor_kvalue_integral,
                    condensor_kvalue_derivative,condensor_temp_st)
condensor_pid.sample_time = 1
condensor_pid.output_limits = (0, 100)

# Main loop
while True:
    # read temperatures and create readalable variables
    temperatures_f = read_temperatures()
    dephlegmator_temp_supply_f = temperatures_f[0]
    dephlegmator_temp_return_f = temperatures_f[1]
    condensor_temp_supply_f = temperatures_f[2]
    condensor_temp_return_f = temperatures_f[3]

    # PID control
    dephlegmator_valve_percent_cmd = dephlegmator_pid(dephlegmator_temp_return_f)
    condensor_valve_percent_cmd = condensor_pid(condensor_temp_return_f)

    # Command valves
    command_valves(dephlegmator_vlv_percent_cmd,condensor_vlv_percent_cmd)

    # Print outputs for trouble shooting
    print("d_vlv({}) d_r_st({}) d_r_t({})".format(
          dephlegmator_vlv_percent_cmd, dephlegmator_temp_st, dephlegmator_temp_return_f))
    print("c_vlv({}) c_r_st({}) c_r_t({})".format(
          condensor_vlv_percent_cmd, condensor_temp_st, condensor_temp_return_f))

    # Insert a delay
    sleep(.5)