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

# Controller Modules
from widgetlords.pi_spi_din import *    # for AI & AO Module
from Widgetloards import *              # for AI module
from simple_pid import pi_spi_din       # PID control library for actuation

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
    valve_outputs.write_single(2,800)
    _ = input("Entered a DA of 800 on Pin-out 2")
    valve_outputs.write_single(2,4000)
    _ = input("Entered a DA of 4000 on Pin-out 0,1")

# ************************************************************************* #
#                                                                           #
#                                   Main Code                               #
#                                                                           #
# ************************************************************************* #

# Define PID objects
# PID for supply
supply_pid = PID(sup_kvalue_proportional,sup_kvalue_integral,sup_kvalue_derivative,supply_temp_st)
supply_pid.sample_time = 1
supply_pid.output_limits = (40, 100)

while True:
    # Test Valve
    test_valve()
