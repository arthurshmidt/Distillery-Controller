import random
from itertools import count
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

plt.style.use('fivethirtyeight')

time_stamp = []
temp_st = []
temp_supply = []
temp_return = []

index = count()

def animate(i):
    data = pd.read_csv('data-gin.csv')
    time_stamp = data['time_stamp']
    temp_st = data['temp_st']
    temp_supply = data['temp_supply']
    temp_return = data['temp_return']

    plt.cla()

    plt.plot(time_stamp,temp_st, label='Temp ST')
    plt.plot(time_stamp,temp_supply, label='Temp Supply')
    plt.plot(time_stamp,temp_return, label='Temp Return')

    plt.legend(loc='upper left')
    plt.tight_layout()

ani = FuncAnimation(plt.gcf(),animate,interval=1000)

plt.tight_layout()
plt.show()
