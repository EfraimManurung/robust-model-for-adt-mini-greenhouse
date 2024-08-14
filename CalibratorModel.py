'''
Calibrator model with Artifical Neural Networks algorithm and glysics based model (the GreenLight model). 
In this class we will combine the ANN model with the GreenLight model.

Author: Efraim Manurung
MSc Thesis in Information Technology Group, Wageningen University

efraim.efraimpartoginahotasi@wur.nl
efraim.manurung@gmail.com

Table 1 Meaning of the state x(t), measurement y(t), control signal u(t) and disturbance d(t).
----------------------------------------------------------------------------------------------------------------------------------
 x1(t) Dry-weight (m2 / m-2)					 y1(t) Dry-weight (m2 / m-2) 
 x2(t) Indoor CO2 (ppm)							 y2(t) Indoor CO2 (ppm)
 x3(t) Indoor temperature (◦C)					 y3(t) Indoor temperature (◦C)
 x4(t) Indoor humidity (%)						 y4(t) Indoor humidity (%)
 x5(t) PAR Inside (W / m2)					     x5(t) PAR Inside (W / m2)
----------------------------------------------------------------------------------------------------------------------------------
 d1(t) Outside Radiation (W / m2)				 u1(t) Fan (-)
 d2(t) Outdoor CO2 (ppm)						 u2(t) Toplighting status (-)
 d3(t) Outdoor temperature (◦C)					 u3(t) Heating (-) 
 
 based on Table 1, we want to predict the state variable x(t) with control signal u(t) and disturbance d(t)
 
 Project sources:
    - Tensor flow
    - 
Other sources:
    -
    -
'''

# IMPORT LIBRARIES for DRL model 
# Import Farama foundation's gymnasium
import gymnasium as gym
from gymnasium.spaces import Box

# Import supporting libraries
import numpy as np
import scipy.io as sio
import os
from datetime import timedelta
import pandas as pd

# IMPORT LIBRARIES for the matlab file
import matlab.engine

# Import service functions
from utils.ServiceFunctions import ServiceFunctions

# IMPORT LIBRARIES for NN model
from utils.ServiceFunctions import ServiceFunctions

import tensorflow as tf
from tensorflow.keras.models import load_model
import joblib
import pandas as pd
import numpy as np
import sklearn.metrics as metrics


class CalibratorModel(gym.Env):
    '''
    Calibrator model that combine a NN model and glysics based model.
    
    Link the Python code to matlab program with related methods. We can link it with the .mat file.
    '''
    
    def __init__(self, env_config):
        '''
        Initialize the environment.
        
        Parameters:
        env_config(dict): Configuration dictionary for the environment.
        '''  
        
        print("Initialized CalibratorModel!")
        
        # Initialize if the main program for training or running
        self.flag_run  = env_config.get("flag_run", True) # The simulation is for running (other option is False for training)
        self.online_measurements = env_config.get("online_measurements", False) # Use online measurements or not from the IoT system 
        self.flag_action_from_drl = env_config.get("flag_action_from_drl", False) # Default is false, and we will use the action from offline datasets
        
        # or just only using offline datasets
        self.first_day_gl = env_config.get("first_day", 1) # The first day of the simulation
        
        # Define the season length parameter
        # 20 minutes
        # But remember, the first 5 minutes is the initial values so
        # only count for the 15 minutes
        # The calculation look like this:
        # 1 / 72 * 24 [hours] * 60 [minutes / hours] = 20 minutes  
        self.season_length_gl = env_config.get("season_length_gl", 1 / 72) #* 3/4
        self.season_length_nn = env_config.get("season_length_nn", 0) # 1 / 72 in matlab is 1 step in this NN model, 20 minutes
        
        # Initiate and max steps
        self.max_steps = env_config.get("max_steps", 4) # How many iteration the program run
    
        # Start MATLAB engine
        self.eng = matlab.engine.start_matlab()

        # Path to MATLAB script
        # Change path based on your directory!
        self.matlab_script_path = r'C:\Users\frm19\OneDrive - Wageningen University & Research\2. Thesis - Information Technology\3. Software Projects\mini-greenhouse-drl-model\matlab\DrlGlEnvironment.m'

        # Load the datasets from separate files for the NN model
        file_path = r"C:\Users\frm19\OneDrive - Wageningen University & Research\2. Thesis - Information Technology\3. Software Projects\mini-greenhouse-greenlight-model\Code\inputs\Mini Greenhouse\dataset7.xlsx"
        
        # Load the dataset
        self.mgh_data = pd.read_excel(file_path)
        
        # Display the first few rows of the dataframe
        print("MiniGreenhouse DATA Columns / Variables (DEBUG): \n")
        print(self.mgh_data.head())
        
        # Initialize lists to store control values
        # if self.flag_action_from_drl == True:
        self.ventilation_list = []
        self.toplights_list = []
        self.heater_list = []
        
        # Initialize a list to store rewards
        self.rewards_list = []
        
        # Initialize reward
        reward = 0
        
        # Record the reward for the first time
        self.rewards_list.extend([reward] * 4)

        # Initialize ServiceFunctions
        self.service_functions = ServiceFunctions()

        # Check if MATLAB script exists
        if os.path.isfile(self.matlab_script_path):
            
            # Initialize lists to store control values
            #if self.flag_action_from_drl == True:
            # Initialize control variables to zero 
            self.init_controls()
            
            # Call the MATLAB function 
            if self.online_measurements == True:
                # Initialize outdoor measurements, to get the outdoor measurements
                self.service_functions.get_outdoor_measurements()
                
                # Run the script with the updated outdoor measurements for the first time
                self.run_matlab_script('outdoor.mat', None, None)
            else:
                # Run the script with empty parameter
                self.run_matlab_script()
        
        else:
            print(f"MATLAB script not found: {self.matlab_script_path}")

        # Predict from the gl model
        self.predicted_inside_measurements_gl()
        
        # Predict from the NN model
        self.predicted_inside_measurements_nn(None)
        
        # Define the observation and action space
        self.define_spaces()
        
        # Initialize the state
        self.reset()
    
    def r2_score_metric(self, y_true, y_pred):
        '''
        Custom R2 score metric
        
        Parameters:
        y_true: tf.Tensor - Ground truth values.
        y_pred: tf.Tensor - Predicted values.
        
        Returns: 
        float: R2 score metric 
        '''
        SS_res =  tf.reduce_sum(tf.square(y_true - y_pred)) 
        SS_tot = tf.reduce_sum(tf.square(y_true - tf.reduce_mean(y_true))) 
        return (1 - SS_res/(SS_tot + tf.keras.backend.epsilon()))
    
    def predict_inside_measurements(self, target_variable, data_input):
        '''
        Predict the measurements or state variables inside mini-greenhouse 
        
        Parameters:
        target_variable: str - The target variable to predict.
        data_input: dict or pd.DataFrame - The input features for the prediction.

        Features (inputs):
            Outside measurements information
                - time
                - global out
                - temp out
                - rh out
                - co2 out
            Control(s) input
                - ventilation
                - toplights
                - heater
        
        Return: 
        np.array: predicted measurements inside mini-greenhouse
        '''
        if isinstance(data_input, dict):
            data_input = pd.DataFrame(data_input)
        
        # Need to be fixed
        features = ['time', 'global out', 'temp out', 'temp out', 'rh out', 'co2 out', 'ventilation', 'toplights', 'heater']
    
        # Ensure the data_input has the required features
        for feature in features:
            if feature not in data_input.columns:
                raise ValueError(f"Missing feature '{feature}' in the input data.")
        
        X_features = data_input[features]
        
        # Load the model using the native Keras format
        loaded_model = load_model(f'nn-model/{target_variable}_model.keras', custom_objects={'r2_score_metric': self.r2_score_metric})
        
        # Load the scalerc:\Users\frm19\OneDrive - Wageningen University & Research\2. Thesis - Information Technology\3. Software Projects\mini-greenhouse-drl-model\main_run.py
        scaler = joblib.load(f'nn-model/{target_variable}_scaler.pkl')
            
        # Scale the input features
        X_features_scaled = scaler.transform(X_features)
        
        # Predict the measurements
        y_hat_measurements = loaded_model.predict(X_features_scaled)
        
        return y_hat_measurements
    
    def define_spaces(self):
        '''
        Define the observation and action spaces.
        
        Based on the observation of the mini-greenhouse system                          Explanation 
            - co2_in: CO2 inside the mini-greenhouse [ppm]
            - temp_in: Temperature inside the mini-greenhouse [°C]
            - rh_in: Relative humidity in percentage in the mini-greenhouse [%]
            - PAR_in: Global radiation inside the mini-greenhouse [W m^{-2}]
            - fruit_leaf: Carbohydrates in leaves [mg{CH2O} m^{-2}]                     Equation 4, 5 [2]
            - fruit_stem: Carbohydrates in stem [mg{CH2O} m^{-2}]                       Equation 6, 7 [2]
            - fruit_dw: Carbohydrates in fruit dry weight [mg{CH2O} m^{-2}]             Equation 2, 3 [2], Equation A44 [5]
            - fruit_cbuf: Carbohydrates in buffer [mg{CH2O} m^{-2}]                     Equation 1, 2 [2]
            - fruit_tcansum: Crop development stage [°C day]                            Equation 8 [2]
        
        The state x(t) variables:
        - Temperature (°C) 
        - Relative Humidity (%) 
        - CO2 Concentration (ppm) 
        - PAR Inside (W/m^2) 
        '''
        
        # Define observation and action spaces
        self.observation_space = Box(
            low=np.array([0.0, 10.00, 0.00, 0.00, 0, 0, 0, -10.00, -10.00]), # In order: CO2, Temperature, Humidity, PAR-in, Fruit Leaf, Fruit Stem, and Fruit Dry Weight
            high=np.array([2000.0, 30.00, 90.00, 25.00, np.inf, np.inf, np.inf, np.inf, np.inf]), 
            dtype=np.float64
        )
        
        self.action_space = Box(
            low=np.array([0, 0, 0], dtype=np.float32), 
            high=np.array([1, 1, 1], dtype=np.float32), 
            dtype=np.float32
        )
        
    def init_controls(self):
        '''
        Initialize control variables.
        '''
        
        # Initialize for the first time 
        time_steps = np.linspace(300, 1200, 4) # 20 minutes (1200 seconds)
        self.controls = {
            'time': time_steps.reshape(-1, 1),
            'ventilation': np.zeros(4).reshape(-1, 1),
            'toplights': np.zeros(4).reshape(-1, 1),
            'heater': np.zeros(4).reshape(-1, 1)
        }
        
        # Append only the latest 3 values from each control variable 
        self.ventilation_list.extend(self.controls['ventilation'].flatten()[-4:])
        self.toplights_list.extend(self.controls['toplights'].flatten()[-4:])
        self.heater_list.extend(self.controls['heater'].flatten()[-4:])
        sio.savemat('controls.mat', self.controls)
        
    def run_matlab_script(self, outdoor_file = None, indoor_file=None, fruit_file=None):
        '''
        Run the MATLAB script.
        '''
        # Check if the outdoor_file or indoor_file or fruit_file is None
        if indoor_file is None:
            indoor_file = []
        
        if fruit_file is None:
            fruit_file = []
        
        if outdoor_file is None:
            outdoor_file = []
        
        self.eng.DrlGlEnvironment(self.season_length_gl, self.first_day_gl, 'controls.mat', outdoor_file, indoor_file, fruit_file, nargout=0)

    def load_excel_data(self, _action_drl):
        '''
        Load data from .xlsx file and store in instance variables.
        
        The data is appended to existing variables if they already exist.
        '''

        # Slice the dataframe to get the rows for the current step
        self.step_data = self.mgh_data.iloc[self.season_length_nn:self.season_length_nn + 4]

        # Extract the required columns and flatten them
        new_time_excel = self.step_data['time'].values
        new_global_out_excel = self.step_data['global out'].values
        new_global_in_excel = self.step_data['global in'].values
        new_temp_in_excel = self.step_data['temp in'].values
        new_temp_out_excel = self.step_data['temp out'].values
        new_rh_in_excel = self.step_data['rh in'].values
        new_rh_out_excel = self.step_data['rh out'].values
        new_co2_in_excel = self.step_data['co2 in'].values
        new_co2_out_excel = self.step_data['co2 out'].values
        
        if self.flag_action_from_drl == True and _action_drl != None:
            # Use the actions from the DRL model
            # Convert actions to discrete values
            ventilation = 1 if _action_drl[0] >= 0.5 else 0
            toplights = 1 if _action_drl[1] >= 0.5 else 0
            heater = 1 if _action_drl[2] >= 0.5 else 0
            
            ventilation = np.full(4, ventilation)
            toplights = np.full(4, toplights)
            heater = np.full(4, heater)

            # Update the step_data with the DRL model's actions
            self.step_data['toplights'] = toplights
            self.step_data['ventilation'] = ventilation
            self.step_data['heater'] = heater
            
            # Add new data
            new_toplights = self.step_data['toplights'].values
            new_ventilation = self.step_data['ventilation'].values
            new_heater = self.step_data['heater'].values   
        else:
            # Use the actions from the offline dataset and
            # add new data
            new_toplights = self.step_data['toplights'].values
            new_ventilation = self.step_data['ventilation'].values
            new_heater = self.step_data['heater'].values

        # Check if instance variables already exist; if not, initialize them
        if not hasattr(self, 'time_excel'):
            self.time_excel = new_time_excel
            self.global_out_excel = new_global_out_excel
            self.global_in_excel = new_global_in_excel
            self.temp_in_excel = new_temp_in_excel
            self.temp_out_excel = new_temp_out_excel
            self.rh_in_excel = new_rh_in_excel
            self.rh_out_excel = new_rh_out_excel
            self.co2_in_excel = new_co2_in_excel
            self.co2_out_excel = new_co2_out_excel
            self.toplights = new_toplights
            self.ventilation = new_ventilation
            self.heater = new_heater
        else:
            # Concatenate new data with existing data
            self.time_excel = np.concatenate((self.time_excel, new_time_excel))
            self.global_out_excel = np.concatenate((self.global_out_excel, new_global_out_excel))
            self.global_in_excel = np.concatenate((self.global_in_excel, new_global_in_excel))
            self.temp_in_excel = np.concatenate((self.temp_in_excel, new_temp_in_excel))
            self.temp_out_excel = np.concatenate((self.temp_out_excel, new_temp_out_excel))
            self.rh_in_excel = np.concatenate((self.rh_in_excel, new_rh_in_excel))
            self.rh_out_excel = np.concatenate((self.rh_out_excel, new_rh_out_excel))
            self.co2_in_excel = np.concatenate((self.co2_in_excel, new_co2_in_excel))
            self.co2_out_excel = np.concatenate((self.co2_out_excel, new_co2_out_excel))
            self.toplights = np.concatenate((self.toplights, new_toplights))
            self.ventilation = np.concatenate((self.ventilation, new_ventilation))
            self.heater = np.concatenate((self.heater, new_heater))
    
    def predicted_inside_measurements_nn(self, _action_drl):
        '''
        Predicted inside measurements
        
        '''
        # Load the updated data from the excel file
        self.load_excel_data(_action_drl)
        
        # Predict the inside measurements (the state variable inside the mini-greenhouse)
        new_par_in_predicted_nn = self.predict_inside_measurements('global in', self.step_data)
        new_temp_in_predicted_nn = self.predict_inside_measurements('temp in', self.step_data)
        new_rh_in_predicted_nn = self.predict_inside_measurements('rh in', self.step_data)
        new_co2_in_predicted_nn = self.predict_inside_measurements('co2 in', self.step_data)
    
        # Check if instance variables already exist; if not, initialize them
        if not hasattr(self, 'temp_in_predicted_nn'):
            self.par_in_predicted_nn = new_par_in_predicted_nn
            self.temp_in_predicted_nn = new_temp_in_predicted_nn
            self.rh_in_predicted_nn = new_rh_in_predicted_nn
            self.co2_in_predicted_nn = new_co2_in_predicted_nn
            
            # print("LENGTH self.par_in_predicted", len(self.par_in_predicted_nn))
            # print("LENGTH self.temp_in_predicted", len(self.temp_in_predicted_nn))
            # print("LENGTH self.rh_in_predicted", len(self.rh_in_predicted_nn))
            # print("LENGTH self.co2_in_predicted", len(self.co2_in_predicted_nn))
        else:
            # Concatenate new data with existing data
            self.par_in_predicted_nn = np.concatenate((self.par_in_predicted_nn, new_par_in_predicted_nn))
            self.temp_in_predicted_nn = np.concatenate((self.temp_in_predicted_nn, new_temp_in_predicted_nn))
            self.rh_in_predicted_nn = np.concatenate((self.rh_in_predicted_nn, new_rh_in_predicted_nn))
            self.co2_in_predicted_nn = np.concatenate((self.co2_in_predicted_nn, new_co2_in_predicted_nn))
            
            # print("self.par_in_predicted", self.par_in_predicted)
            # print("self.temp_in_predicted", self.temp_in_predicted)
            # print("self.rh_in_predicted", self.rh_in_predicted)
            # print("self.co2_in_predicted", self.co2_in_predicted)
            
            print("LENGTH self.par_in_predicted", len(self.par_in_predicted_nn))
            print("LENGTH self.temp_in_predicted", len(self.temp_in_predicted_nn))
            print("LENGTH self.rh_in_predicted", len(self.rh_in_predicted_nn))
            print("LENGTH self.co2_in_predicted", len(self.co2_in_predicted_nn))
    
    def predicted_inside_measurements_gl(self):
        '''
        Load data from the .mat file.
        
        From matlab, the structure is:
        
        save('drl-env.mat', 'time', 'temp_in', 'rh_in', 'co2_in', 'PAR_in', 'fruit_leaf', 'fruit_stem', 'fruit_dw');
        '''
        
        # Read the drl-env mat from the initialization 
        # Read the 3 values and append it
        data = sio.loadmat("drl-env.mat")
        
        new_time_gl = data['time'].flatten()[-4:]
        new_co2_in_predicted_gl = data['co2_in'].flatten()[-4:]
        new_temp_in_predicted_gl = data['temp_in'].flatten()[-4:]
        new_rh_in_predicted_gl = data['rh_in'].flatten()[-4:]
        new_PAR_in_predicted_gl = data['PAR_in'].flatten()[-4:]
        new_fruit_leaf_predicted_gl = data['fruit_leaf'].flatten()[-4:]
        new_fruit_stem_predicted_gl = data['fruit_stem'].flatten()[-4:]
        new_fruit_dw_predicted_gl = data['fruit_dw'].flatten()[-4:]
        new_fruit_cbuf_predicted_gl = data['fruit_cbuf'].flatten()[-4:]
        new_fruit_tcansum_predicted_gl = data['fruit_tcansum'].flatten()[-4:]

        if not hasattr(self, 'time_gl'):
            self.time_gl = new_time_gl
            self.co2_in_predicted_gl = new_co2_in_predicted_gl
            self.temp_in_predicted_gl = new_temp_in_predicted_gl
            self.rh_in_predicted_gl = new_rh_in_predicted_gl
            self.PAR_in_predicted_gl = new_PAR_in_predicted_gl
            self.fruit_leaf_predicted_gl = new_fruit_leaf_predicted_gl
            self.fruit_stem_predicted_gl = new_fruit_stem_predicted_gl
            self.fruit_dw_predicted_gl = new_fruit_dw_predicted_gl
            self.fruit_cbuf_predicted_gl = new_fruit_cbuf_predicted_gl
            self.fruit_tcansum_predicted_gl = new_fruit_tcansum_predicted_gl
        else:
            self.time_gl = np.concatenate((self.time_gl, new_time_gl))
            self.co2_in_predicted_gl = np.concatenate((self.co2_in_predicted_gl, new_co2_in_predicted_gl))
            self.temp_in_predicted_gl = np.concatenate((self.temp_in_predicted_gl, new_temp_in_predicted_gl))
            self.rh_in_predicted_gl= np.concatenate((self.rh_in_predicted_gl, new_rh_in_predicted_gl))
            self.PAR_in_predicted_gl = np.concatenate((self.PAR_in_predicted_gl, new_PAR_in_predicted_gl))
            self.fruit_leaf_predicted_gl = np.concatenate((self.fruit_leaf_predicted_gl, new_fruit_leaf_predicted_gl))
            self.fruit_stem_predicted_gl = np.concatenate((self.fruit_stem_predicted_gl, new_fruit_stem_predicted_gl))
            self.fruit_dw_predicted_gl = np.concatenate((self.fruit_dw_predicted_gl, new_fruit_dw_predicted_gl))
            self.fruit_cbuf_predicted_gl = np.concatenate((self.fruit_cbuf_predicted_gl, new_fruit_cbuf_predicted_gl))
            self.fruit_tcansum_predicted_gl = np.concatenate((self.fruit_tcansum_predicted_gl, new_fruit_tcansum_predicted_gl))
        
    def reset(self, *, seed=None, options=None):
        '''
        Reset the environment to the initial state.
        
        Returns:
        int: The initial observation of the environment.
        '''
        self.current_step = 0
        self.season_length_nn += 4
        
        return self.observation(), {}

    def observation(self):
        '''
        Get the observation of the environment for every state.
        
        Returns:
        array: The observation space of the environment.
        '''
        
        # return np.array([
        #     self.co2_in[-1],
        #     self.temp_in[-1],
        #     self.rh_in[-1],
        #     self.global_in[-1],
        #     self.global_out[-1],
        #     self.temp_out[-1],
        #     self.rh_out[-1],
        #     self.toplights[-1],
        #     self.ventilation[-1],
        #     self.heater[-1],
        #     self.par_in_predicted[-1][0],  # Ensure this is a scalar value
        #     self.temp_in_predicted[-1][0], # Ensure this is a scalar value
        #     self.rh_in_predicted[-1][0],   # Ensure this is a scalar value
        #     self.co2_in_predicted[-1][0]   # Ensure this is a scalar value
        # ], np.float32)
        
        return np.array([
            self.co2_in_predicted_gl[-1],
            self.temp_in_predicted_gl[-1],
            self.rh_in_predicted_gl[-1],
            self.PAR_in_predicted_gl[-1],
            self.fruit_leaf_predicted_gl[-1],
            self.fruit_stem_predicted_gl[-1],
            self.fruit_dw_predicted_gl[-1],
            self.fruit_cbuf_predicted_gl[-1],
            self.fruit_tcansum_predicted_gl[-1]
        ], np.float32) 

    def get_reward(self, _ventilation, _toplights, _heater):
        '''
        Get the reward for the current state.
        
        The reward function defines the immediate reward obtained by the agent for its actions in a given state. 
        Changed based on the MiniGreenhouse environment from the equation (4) in the source.
        
        Source: Bridging the reality gap: An Adaptive Framework for Autonomous Greenhouse 
        Control with Deep Reinforcement Learning. George Qi. Wageningen University. 2024.
        
        r(k) = w_r,y1 * Δy1(k) - Σ (from i=1 to 3) w_r,ai * ai(k) 
        
        Details 
        r(k): the imediate reward the agent receives at time step k.
        w_r,y1 * Δy1(k): represents the positive reward for the agent due to increased in the fruit dry weight Δy1(k).
        Σ (from i=1 to 3) w_r,ai * ai(k): represents the negative reward received by the agent due to cost energy with arbitrary 

        Obtained coefficient setting for the reward function.
        Coefficients        Values          Details
        w_r_y1              1               Fruit dry weight 
        w_r_a1              0.005           Ventilation
        w_r_a2              0.015           Toplights
        w_r_a3              0.001           Heater
        
        Returns:
        int: the immediate reward the agent receives at time step k in integer.
        '''
        
        # Initialize variables, based on the equation above
        # Need to be determined to make the r_k unitless
        w_r_y1 = 1          # Fruit dry weight 
        w_r_a1 = 0.005      # Ventilation
        w_r_a2 = 0.015      # Toplights
        w_r_a3 = 0.001      # Heater
        
        # Give initial reward 
        if self.current_step == 0:
            r_k = 0.0
            return r_k # No reward for the initial state 
        
        # In the createCropModel.m in the GreenLight model (mini-greenhouse-model)
        # cFruit or dry weight of fruit is the carbohydrates in fruit, so it is the best variable to count for the reward
        # Calculate the change in fruit dry weight
        delta_fruit_dw = (self.fruit_dw_predicted_gl[-1] - self.fruit_dw_predicted_gl[-2])
        print("delta_fruit_dw: ", delta_fruit_dw)
        
        r_k = w_r_y1 * delta_fruit_dw - ((w_r_a1 * _ventilation) + (w_r_a2 * _toplights) + (w_r_a3 * _heater))
        print("r_k immediate reward: ", r_k)
        
        return r_k
        
    def delete_files(self):
        '''
        delete matlab files after simulation to make it clear.    
        '''
        os.remove('controls.mat') # controls file
        os.remove('drl-env.mat')  # simulation file
        os.remove('indoor.mat')   # indoor measurements
        os.remove('fruit.mat')    # fruit growth

        if self.online_measurements == True:
            os.remove('outdoor.mat')  # outdoor measurements
        
    def done(self):
        '''
        Check if the episode is done.
        
        Returns:
        bool: True if the episode is done, otherwise False.
        '''
        
        # Episode is done if we have reached the target
        # We print all the physical parameters and controls

        if self.flag_run == True:
            # Terminated when current step is same value with max_steps 
            # In one episode, for example if the max_step = 4, that mean 1 episode is for 1 hour in real-time (real-measurements)
            if self.current_step >= self.max_steps:
                
                # Print and save all data
                if self.online_measurements == True:
                    self.print_and_save_all_data('output/output_simulated_data_online.xlsx')
                else:
                    self.print_and_save_all_data('output/output_simulated_data_offline.xlsx')
                
                # Delete all files
                self.delete_files()
                
                return True
        else:
            if self.current_step >= self.max_steps:
                # Delete all files
                # self.delete_files()
                return True
            
        return False

    def step(self, _action_drl = None):
        '''
        Take an action in the environment.
        
        Parameters:
        
        Based on the u(t) controls
        
        action (discrete integer):
        -  u1(t) Ventilation (-)               0-1 (1 is fully open) 
        -  u2(t) Toplights (-)                 0/1 (1 is on)
        -  u3(t) Heater (-)                    0/1 (1 is on)

        Returns:
            New observation, reward, terminated-flag (frome done method), truncated-flag, info-dict (empty).
        '''
         
        # Increment the current step
        self.current_step += 1
        print("")
        print("")
        print("----------------------------------")
        print("CURRENT STEPS: ", self.current_step)
        
        # Call the predicted inside measurements with the NN model
        self.predicted_inside_measurements_nn(_action_drl)
        
        if self.flag_action_from_drl == True:
            # Get the action from the DRL model 
            print("ACTION: ", _action_drl)
            
            # Convert actions to discrete values
            ventilation = 1 if _action_drl[0] >= 0.5 else 0
            toplights = 1 if _action_drl[1] >= 0.5 else 0
            heater = 1 if _action_drl[2] >= 0.5 else 0
            
            time_steps = np.linspace(300, 1200, 4)  # Time steps in seconds
            ventilation = np.full(4, ventilation)
            toplights = np.full(4, toplights)
            heater = np.full(4, heater)

        else:
            # Get the actions from the excel file (offline datasets)
            time_steps = np.linspace(300, 1200, 4)  # Time steps in seconds
            ventilation = self.ventilation[-4:]
            toplights = self.toplights[-4:]
            heater = self.heater[-4:]
                                 
        print("CONVERTED ACTION")
        print("ventilation: ", ventilation)
        print("toplights: ", toplights)
        print("heating: ", heater)

        # Keep only the latest 3 data points before appending
        # Append controls to the lists
        self.ventilation_list.extend(ventilation[-4:])
        self.toplights_list.extend(toplights[-4:])
        self.heater_list.extend(heater[-4:])
        
        # Only publish MQTT data for the Raspberry Pi when running not training
        if self.online_measurements == True:
            # Format data controls in JSON format
            json_data = self.service_functions.format_data_in_JSON(time_steps, \
                                                ventilation, toplights, \
                                                heater)
            
            # Publish controls to the raspberry pi (IoT system client)
            self.service_functions.publish_mqtt_data(json_data)

        # Create control dictionary
        controls = {
            'time': time_steps.reshape(-1, 1),
            'ventilation': ventilation.reshape(-1, 1),
            'toplights': toplights.reshape(-1, 1),
            'heater': heater.reshape(-1, 1)
        }
        
        # Save control variables to .mat file
        sio.savemat('controls.mat', controls)
        
        # Update the season_length and first_day for the GL model
        # 1 / 72 is 20 minutes in 24 hours, the calculation look like this
        # 1 / 72 * 24 [hours] * 60 [minutes . hours ^ -1] = 20 minutes 
        self.season_length_gl = 1 / 72 
        self.first_day_gl += 1 / 72 
        
        # Update the season_length for the NN model
        self.season_length_nn += 4

        # Convert co2_in ppm
        co2_density_gl = self.service_functions.co2ppm_to_dens(self.temp_in_predicted_gl[-4:], self.co2_in_predicted_gl[-4:])
        
        # Convert Relative Humidity (RH) to Pressure in Pa
        vapor_density_gl = self.service_functions.rh_to_vapor_density(self.temp_in_predicted_gl[-4:], self.rh_in_predicted_gl[-4:])
        vapor_pressure_gl = self.service_functions.vapor_density_to_pressure(self.temp_in_predicted_gl[-4:], vapor_density_gl)

        # Update the MATLAB environment with the 3 latest current state
        # It will be used to be simulated in the GreenLight model with mini-greenhouse parameters
        drl_indoor = {
            'time': self.time_gl[-3:].astype(float).reshape(-1, 1),
            'temp_in': self.temp_in_predicted_gl[-3:].astype(float).reshape(-1, 1),
            'rh_in': vapor_pressure_gl[-3:].astype(float).reshape(-1, 1),
            'co2_in': co2_density_gl[-3:].astype(float).reshape(-1, 1)
        }
        
        # Save control variables to .mat file
        sio.savemat('indoor.mat', drl_indoor)

        # Update the fruit growth with the 1 latest current state from the GreenLight model - mini-greenhouse parameters
        fruit_growth = {
            'time': self.time_gl[-1:].astype(float).reshape(-1, 1),
            'fruit_leaf': self.fruit_leaf_predicted_gl[-1:].astype(float).reshape(-1, 1),
            'fruit_stem': self.fruit_stem_predicted_gl[-1:].astype(float).reshape(-1, 1),
            'fruit_dw': self.fruit_dw_predicted_gl[-1:].astype(float).reshape(-1, 1),
            'fruit_cbuf': self.fruit_cbuf_predicted_gl[-1:].astype(float).reshape(-1, 1),
            'fruit_tcansum': self.fruit_tcansum_predicted_gl[-1:].astype(float).reshape(-1, 1)
        }

        # Save the fruit growth to .mat file
        sio.savemat('fruit.mat', fruit_growth)
        
        if self.online_measurements == True:
            # Get the outdoor measurements
            self.service_functions.get_outdoor_measurements()

        # Run the script with the updated state variables
        if self.online_measurements == True:
            self.run_matlab_script('outdoor.mat', 'indoor.mat', 'fruit.mat')
        else:
            self.run_matlab_script(None, 'indoor.mat', 'fruit.mat')
        
        # Load the updated data from predcited from the greenlight model
        self.predicted_inside_measurements_gl()
        
        # Calculate reward
        # Remember that the actions become a list, but we only need the first actions from 15 minutes (all of the is the same)
        _reward = self.get_reward(ventilation[0], toplights[0], heater[0])
        
        # Record the reward
        self.rewards_list.extend([_reward] * 4)

        # Truncated flag
        truncated = False
    
        return self.observation(), _reward, self.done(), truncated, {}
    
    def print_and_save_all_data(self, file_name):
        '''
        Print all the appended data and save to an Excel file and plot it.

        Parameters:
        - file_name: Name of the output Excel file
        
        - ventilation_list: List of action for fan/ventilation from DRL model or offline datasets
        - toplights_list: List of action for toplights from DRL model or offline datasets
        - heater_list: List of action for heater from DRL model or offline datasets
        - reward_list: List of reward for iterated step
        
        - co2_in_excel: List of actual CO2 values
        - temp_in_excel: List of actual temperature values
        - rh_in_excel: List of actual relative humidity values
        - par_in_excel: List of actual PAR values
        
        - co2_in_predicted_nn: List of predicted CO2 values from Neural Network
        - temp_in_predicted_nn: List of predicted temperature values from Neural Network
        - rh_in_predicted_nn: List of predicted relative humidity values from Neural Network
        - par_in_predicted_nn: List of predicted PAR values from Neural Network
        
        - co2_in_predicted_gl: List of predicted CO2 values from Generalized Linear Model
        - temp_in_predicted_gl: List of predicted temperature values from Generalized Linear Model
        - rh_in_predicted_gl: List of predicted relative humidity values from Generalized Linear Model
        - par_in_predicted_gl: List of predicted PAR values from Generalized Linear Model
        '''
        
        print("\n\n-------------------------------------------------------------------------------------")
        print("Print all the appended data.")
        print(f"Length of Time: {len(self.time_excel)}")
        print(f"Length of Action Ventilation: {len(self.ventilation_list)}")
        print(f"Length of Action Toplights: {len(self.toplights_list)}")
        print(f"Length of Action Heater: {len(self.heater_list)}")
        print(f"Length of reward: {len(self.rewards_list)}")
        print(f"Length of CO2 In (Actual): {len(self.co2_in_excel)}")
        print(f"Length of Temperature In (Actual): {len(self.temp_in_excel)}")
        print(f"Length of RH In (Actual): {len(self.rh_in_excel)}")
        print(f"Length of PAR In (Actual): {len(self.global_in_excel)}")
        print(f"Length of Predicted CO2 In (NN): {len(self.co2_in_predicted_nn)}")
        print(f"Length of Predicted Temperature In (NN): {len(self.temp_in_predicted_nn)}")
        print(f"Length of Predicted RH In (NN): {len(self.rh_in_predicted_nn)}")
        print(f"Length of Predicted PAR In (NN): {len(self.par_in_predicted_nn)}")
        print(f"Length of Predicted CO2 In (GL): {len(self.co2_in_predicted_gl)}")
        print(f"Length of Predicted Temperature In (GL): {len(self.temp_in_predicted_gl)}")
        print(f"Length of Predicted RH In (GL): {len(self.rh_in_predicted_gl)}")
        print(f"Length of Predicted PAR In (GL): {len(self.PAR_in_predicted_gl)}")

        time_steps_formatted = range(0, self.season_length_nn)
        print("Time Steps Formatted: ", time_steps_formatted)
        
        # Evaluate predictions to get R² and MAE metrics
        metrics_nn, metrics_gl = self.evaluate_predictions()
        
        # Save all the data in an Excel file
        self.service_functions.export_to_excel(
            file_name, time_steps_formatted, self.ventilation_list, self.toplights_list, self.heater_list, self.rewards_list,
            self.co2_in_excel, self.temp_in_excel, self.rh_in_excel, self.global_in_excel,
            self.co2_in_predicted_nn[:, 0], self.temp_in_predicted_nn[:, 0], self.rh_in_predicted_nn[:, 0], self.par_in_predicted_nn[:, 0],
            self.co2_in_predicted_gl, self.temp_in_predicted_gl, self.rh_in_predicted_gl, self.PAR_in_predicted_gl
        )

        # Plot the data
        self.service_functions.plot_all_data(
            'output/output_all_data.png', time_steps_formatted, 
            self.co2_in_excel, self.temp_in_excel, self.rh_in_excel, self.global_in_excel,
            self.co2_in_predicted_nn[:, 0], self.temp_in_predicted_nn[:, 0], self.rh_in_predicted_nn[:, 0], self.par_in_predicted_nn[:, 0],
            self.co2_in_predicted_gl, self.temp_in_predicted_gl, self.rh_in_predicted_gl, self.PAR_in_predicted_gl,
            metrics_nn, metrics_gl
        )
        
        # Plot the rewards and actions
        self.service_functions.plot_rewards_actions('output/output_rewards_action.png', time_steps_formatted, self.ventilation_list, self.toplights_list, 
                                                    self.heater_list, self.rewards_list)


    def evaluate_predictions(self):
        '''
        Evaluate the R² and MAE of the predicted vs actual values for `par_in`, `temp_in`, `rh_in`, and `co2_in`.
        '''
        
        # Extract actual values
        y_true_par_in = self.global_in_excel
        y_true_temp_in = self.temp_in_excel
        y_true_rh_in = self.rh_in_excel
        y_true_co2_in = self.co2_in_excel

        # Extract predicted values
        y_pred_par_in_nn = self.par_in_predicted_nn[:, 0]
        y_pred_temp_in_nn = self.temp_in_predicted_nn[:, 0]
        y_pred_rh_in_nn = self.rh_in_predicted_nn[:, 0]
        y_pred_co2_in_nn = self.co2_in_predicted_nn[:, 0]
        
        y_pred_par_in_gl = self.PAR_in_predicted_gl
        y_pred_temp_in_gl = self.temp_in_predicted_gl
        y_pred_rh_in_gl = self.rh_in_predicted_gl
        y_pred_co2_in_gl = self.co2_in_predicted_gl

        # Calculate R² and MAE for each variable
        def calculate_metrics(y_true, y_pred):
            r2 = metrics.r2_score(y_true, y_pred)
            mae = metrics.mean_absolute_error(y_true, y_pred)
            return r2, mae

        metrics_nn = {
            'PAR': calculate_metrics(y_true_par_in, y_pred_par_in_nn),
            'Temperature': calculate_metrics(y_true_temp_in, y_pred_temp_in_nn),
            'Humidity': calculate_metrics(y_true_rh_in, y_pred_rh_in_nn),
            'CO2': calculate_metrics(y_true_co2_in, y_pred_co2_in_nn)
        }

        metrics_gl = {
            'PAR': calculate_metrics(y_true_par_in, y_pred_par_in_gl),
            'Temperature': calculate_metrics(y_true_temp_in, y_pred_temp_in_gl),
            'Humidity': calculate_metrics(y_true_rh_in, y_pred_rh_in_gl),
            'CO2': calculate_metrics(y_true_co2_in, y_pred_co2_in_gl)
        }

        # Print the results
        print("Evaluation Results:")
        for variable in ['PAR', 'Temperature', 'Humidity', 'CO2']:
            r2_nn, mae_nn = metrics_nn[variable]
            r2_gl, mae_gl = metrics_gl[variable]
            print(f"{variable} (NN): R² = {r2_nn:.4f}, MAE = {mae_nn:.4f}")
            print(f"{variable} (GL): R² = {r2_gl:.4f}, MAE = {mae_gl:.4f}")

        return metrics_nn, metrics_gl

    # Ensure to properly close the MATLAB engine when the environment is no longer used
    def __del__(self):
        self.eng.quit()
        

