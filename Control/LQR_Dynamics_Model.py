"""
LQR controller for autonomous vehicle
@author: huiming zhou (zhou.hm0420@gmail.com)

This controller is the python version of LQR controller of Apollo.
GitHub link of BaiDu Apollo: https://github.com/ApolloAuto/apollo

Modules in this file:
[Path Planner: ] Hybrid A*
[Lateral Controller: ] LQR (parameters from Apollo)
[Longitudinal Controller: ] PID
"""

import os
import sys
import math
import numpy as np
from enum import Enum
import matplotlib.pyplot as plt

sys.path.append(os.path.dirname(os.path.abspath(__file__)) +
                "/../../MotionPlanning/")

import HybridAstarPlanner.hybrid_astar as HybridAStar
import HybridAstarPlanner.draw as draw
import CurvesGenerator.cubic_spline as cs
from Control.lateral_controller_conf import *


class Gear(Enum):
    GEAR_DRIVE = 1
    GEAR_REVERSE = 2


class VehicleState:
    def __init__(self, x=0.0, y=0.0, yaw=0.0,
                 v=0.0, gear=Gear.GEAR_DRIVE):
        self.x = x
        self.y = y
        self.yaw = yaw
        self.v = v
        self.e_cg = 0.0
        self.theta_e = 0.0

        self.gear = gear

    def UpdateVehicleState(self, delta, a, e_cg, theta_e,
                           gear=Gear.GEAR_DRIVE):
        """
        update states of vehicle

        :param theta_e: yaw error to ref trajectory
        :param e_cg: lateral error to ref trajectory
        :param delta: steering angle [rad]
        :param a: acceleration [m / s^2]
        :param gear: gear mode [GEAR_DRIVE / GEAR/REVERSE]
        """

        wheelbase_ = l_r + l_f
        delta, a = self.RegulateInput(delta, a)

        self.gear = gear
        self.x += self.v * math.cos(self.yaw) * ts
        self.y += self.v * math.sin(self.yaw) * ts
        self.yaw += self.v / wheelbase_ * math.tan(delta) * ts
        self.e_cg = e_cg
        self.theta_e = theta_e

        if gear == Gear.GEAR_DRIVE:
            self.v += a * ts
        else:
            self.v += -1.0 * a * ts

        self.v = self.RegulateOutput(self.v)

    @staticmethod
    def RegulateInput(delta, a):
        """
        regulate delta to : - max_steer_angle ~ max_steer_angle
        regulate a to : - max_acceleration ~ max_acceleration

        :param delta: steering angle [rad]
        :param a: acceleration [m / s^2]
        :return: regulated delta and acceleration
        """

        if delta < -1.0 * max_steer_angle:
            delta = -1.0 * max_steer_angle

        if delta > 1.0 * max_steer_angle:
            delta = 1.0 * max_steer_angle

        if a < -1.0 * max_acceleration:
            a = -1.0 * max_acceleration

        if a > 1.0 * max_acceleration:
            a = 1.0 * max_acceleration

        return delta, a

    @staticmethod
    def RegulateOutput(v):
        """
        regulate v to : -max_speed ~ max_speed

        :param v: calculated speed [m / s]
        :return: regulated speed
        """

        max_speed_ = max_speed / 3.6

        if v < -1.0 * max_speed_:
            v = -1.0 * max_speed_

        if v > 1.0 * max_speed_:
            v = 1.0 * max_speed_

        return v


class TrajectoryAnalyzer:
    def __init__(self, x, y, yaw, k):
        self.x_ = x
        self.y_ = y
        self.yaw_ = yaw
        self.k_ = k

        self.ind_old = 0
        self.ind_end = len(x)

    def ToTrajectoryFrame(self, vehicle_state):
        """
        errors to trajectory frame

        theta_e = yaw_vehicle - yaw_ref_path
        e_cg = lateral distance of center of gravity (cg) in frenet frame

        :param vehicle_state: vehicle state (class VehicleState)
        :return: theta_e, e_cg, yaw_ref, k_ref
        """

        x_cg = vehicle_state.x
        y_cg = vehicle_state.y
        yaw = vehicle_state.yaw

        # calc nearest point in ref path
        dx = [x_cg - ix for ix in self.x_[self.ind_old: self.ind_end]]
        dy = [y_cg - iy for iy in self.y_[self.ind_old: self.ind_end]]

        ind_add = int(np.argmin(np.hypot(dx, dy)))
        dist = math.hypot(dx[ind_add], dy[ind_add])

        # calc lateral relative position of vehicle to ref path
        vec_axle_rot_90 = np.array([[math.cos(yaw + math.pi / 2.0)],
                                    [math.sin(yaw + math.pi / 2.0)]])

        vec_path_2_cg = np.array([[dx[ind_add]],
                                  [dy[ind_add]]])

        if np.dot(vec_axle_rot_90.T, vec_path_2_cg) > 0.0:
            e_cg = 1.0 * dist  # vehicle on the right of ref path
        else:
            e_cg = -1.0 * dist  # vehicle on the left of ref path

        # calc yaw error: theta_e = yaw_vehicle - yaw_ref
        self.ind_old += ind_add
        yaw_ref = self.yaw_[self.ind_old]
        theta_e = pi_2_pi(yaw - yaw_ref)

        # calc ref curvature
        k_ref = self.k_[self.ind_old]

        return theta_e, e_cg, yaw_ref, k_ref


class LatController:
    """
    Lateral Controller using LQR
    """

    def ComputeControlCommand(self, vehicle_state, ref_trajectory):
        """
        calc lateral control command.
        :param vehicle_state: vehicle state
        :param ref_trajectory: reference trajectory (analyzer)
        :return: steering angle (optimal u), theta_e, e_cg
        """

        ts_ = ts
        e_cg_old = vehicle_state.e_cg
        theta_e_old = vehicle_state.theta_e

        theta_e, e_cg, yaw_ref, k_ref = \
            ref_trajectory.ToTrajectoryFrame(vehicle_state)

        matrix_ad_, matrix_bd_ = self.UpdateMatrix(vehicle_state)

        matrix_state_ = np.zeros((state_size, 1))
        matrix_r_ = np.diag(matrix_r)
        matrix_q_ = np.diag(matrix_q)

        matrix_k_ = self.SolveLQRProblem(matrix_ad_, matrix_bd_, matrix_q_,
                                         matrix_r_, eps, max_iteration)

        matrix_state_[0][0] = e_cg
        matrix_state_[1][0] = (e_cg - e_cg_old) / ts_
        matrix_state_[2][0] = theta_e
        matrix_state_[3][0] = (theta_e - theta_e_old) / ts_

        steer_angle_feedback = -(matrix_k_ @ matrix_state_)[0][0]

        steer_angle_feedforward = self.ComputeFeedForward(vehicle_state, k_ref, matrix_k_)

        steer_angle = steer_angle_feedback + steer_angle_feedforward

        return steer_angle, theta_e, e_cg

    @staticmethod
    def ComputeFeedForward(vehicle_state, ref_curvature, matrix_k_):
        """
        calc feedforward control term to decrease the steady error.
        :param vehicle_state: vehicle state
        :param ref_curvature: curvature of the target point in ref trajectory
        :param matrix_k_: feedback matrix K
        :return: feedforward term
        """

        mass_ = m_f + m_r
        wheelbase_ = l_f + l_r

        kv = l_r * mass_ / 2.0 / c_f / wheelbase_ - \
             l_f * mass_ / 2.0 / c_r / wheelbase_

        v = vehicle_state.v

        if vehicle_state.gear == Gear.GEAR_REVERSE:
            steer_angle_feedforward = wheelbase_ * ref_curvature
        else:
            steer_angle_feedforward = wheelbase_ * ref_curvature + kv * v * v * ref_curvature - \
                                      matrix_k_[0][2] * \
                                      (l_r * ref_curvature -
                                       l_f * mass_ * v * v * ref_curvature / 2.0 / c_r / wheelbase_)

        return steer_angle_feedforward

    @staticmethod
    def SolveLQRProblem(A, B, Q, R, tolerance, max_num_iteration):
        """
        iteratively calculating feedback matrix K

        :param A: matrix_a_
        :param B: matrix_b_
        :param Q: matrix_q_
        :param R: matrix_r_
        :param tolerance: lqr_eps
        :param max_num_iteration: max_iteration
        :return: feedback matrix K
        """

        assert np.size(A, 0) == np.size(A, 1) and \
               np.size(B, 0) == np.size(A, 0) and \
               np.size(Q, 0) == np.size(Q, 1) and \
               np.size(Q, 0) == np.size(A, 1) and \
               np.size(R, 0) == np.size(R, 1) and \
               np.size(R, 0) == np.size(B, 1), \
            "LQR solver: one or more matrices have incompatible dimensions."

        M = np.zeros((np.size(Q, 0), np.size(R, 1)))

        AT = A.T
        BT = B.T
        MT = M.T

        P = Q
        num_iteration = 0
        diff = math.inf

        while num_iteration < max_num_iteration and diff > tolerance:
            num_iteration += 1
            P_next = AT @ P @ A - \
                     (AT @ P @ B + M) @ np.linalg.pinv(R + BT @ P @ B) @ (BT @ P @ A + MT) + Q

            # check the difference between P and P_next
            diff = (abs(P_next - P)).max()
            P = P_next

        if num_iteration >= max_num_iteration:
            print("LQR solver cannot converge to a solution",
                  "last consecutive result diff is: ", diff)

        K = np.linalg.inv(BT @ P @ B + R) @ (BT @ P @ A + MT)

        return K

    @staticmethod
    def UpdateMatrix(vehicle_state):
        """
        calc A and b matrices of linearized, discrete system.
        :return: A, b
        """

        ts_ = ts
        mass_ = m_f + m_r

        v = vehicle_state.v

        matrix_a_ = np.zeros((state_size, state_size))  # continuous A matrix

        if vehicle_state.gear == Gear.GEAR_REVERSE:
            """
            A matrix (Gear Reverse)
            [0.0, 0.0, 1.0 * v 0.0;
             0.0, -(c_f + c_r) / m / v, (c_f + c_r) / m,
             (l_r * c_r - l_f * c_f) / m / v;
             0.0, 0.0, 0.0, 1.0;
             0.0, (lr * cr - lf * cf) / i_z / v, (l_f * c_f - l_r * c_r) / i_z,
             -1.0 * (l_f^2 * c_f + l_r^2 * c_r) / i_z / v;]
            """

            matrix_a_[0][1] = 0.0
            matrix_a_[0][2] = 1.0 * v
        else:
            """
            A matrix (Gear Drive)
            [0.0, 1.0, 0.0, 0.0;
             0.0, -(c_f + c_r) / m / v, (c_f + c_r) / m,
             (l_r * c_r - l_f * c_f) / m / v;
             0.0, 0.0, 0.0, 1.0;
             0.0, (lr * cr - lf * cf) / i_z / v, (l_f * c_f - l_r * c_r) / i_z,
             -1.0 * (l_f^2 * c_f + l_r^2 * c_r) / i_z / v;]
            """

            matrix_a_[0][1] = 1.0
            matrix_a_[0][2] = 0.0

        matrix_a_[1][1] = -1.0 * (c_f + c_r) / mass_ / v
        matrix_a_[1][2] = (c_f + c_r) / mass_
        matrix_a_[1][3] = (l_r * c_r - l_f * c_f) / mass_ / v
        matrix_a_[2][3] = 1.0
        matrix_a_[3][1] = (l_r * c_r - l_f * c_f) / Iz / v
        matrix_a_[3][2] = (l_f * c_f - l_r * c_r) / Iz
        matrix_a_[3][3] = -1.0 * (l_f ** 2 * c_f + l_r ** 2 * c_r) / Iz / v

        # Tustin's method (bilinear transform)
        matrix_i = np.eye(state_size)  # identical matrix
        matrix_ad_ = np.linalg.pinv(matrix_i - ts_ * 0.5 * matrix_a_) @ \
                     (matrix_i + ts_ * 0.5 * matrix_a_)  # discrete A matrix

        # b = [0.0, c_f / m, 0.0, l_f * c_f / I_z].T
        matrix_b_ = np.zeros((state_size, 1))  # continuous b matrix
        matrix_b_[1][0] = c_f / mass_
        matrix_b_[3][0] = l_f * c_f / Iz
        matrix_bd_ = matrix_b_ * ts_  # discrete b matrix

        return matrix_ad_, matrix_bd_


class LonController:
    """
    Longitudinal Controller using PID.
    """

    @staticmethod
    def ComputeControlCommand(target_speed, vehicle_state, dist):
        """
        calc acceleration command using PID.
        :param target_speed: target speed [m / s]
        :param vehicle_state: vehicle state
        :param dist: distance to goal [m]
        :return: control command (acceleration) [m / s^2]
        """

        a = 0.3 * (target_speed - vehicle_state.v)

        if dist < 11.0:
            if vehicle_state.v > 2.0:
                a = -3.0
            elif vehicle_state.v < -2:
                a = -1.0

        return a


def pi_2_pi(angle):
    """
    regulate theta to -pi ~ pi.
    :param angle: input angle
    :return: regulated angle
    """

    M_PI = math.pi

    if angle > M_PI:
        return angle - 2.0 * M_PI

    if angle < -M_PI:
        return angle + 2.0 * M_PI

    return angle


def main():
    ax = np.arange(0, 50, 0.5)
    ay = [math.sin(ix / 5.0) * ix / 3.0 for ix in ax]

    x, y, yaw, k, _ = cs.calc_spline_course(ax, ay, ds=ts)

    ref_trajectory = TrajectoryAnalyzer(x, y, yaw, k)

    vehicle_state = VehicleState(x=x[0], y=y[0], yaw=yaw[0], v=1.0, gear=Gear.GEAR_DRIVE)

    lat_controller = LatController()
    lon_controller = LonController()

    time = 0.0
    max_simulation_time = 500.0

    target_speed = 25.0 / 3.6  # [m / s^2]
    x_goal = x[-1]
    y_goal = y[-1]

    x_rec = []
    y_rec = []
    yaw_rec = []

    while time < max_simulation_time:
        time += ts

        dist = math.hypot(vehicle_state.x - x_goal, vehicle_state.y - y_goal)

        delta_opt, theta_e, e_cg = \
            lat_controller.ComputeControlCommand(vehicle_state, ref_trajectory)

        a_opt = \
            lon_controller.ComputeControlCommand(target_speed, vehicle_state, dist)

        vehicle_state.UpdateVehicleState(delta_opt, a_opt, e_cg, theta_e, Gear.GEAR_DRIVE)

        x_rec.append(vehicle_state.x)
        y_rec.append(vehicle_state.y)
        yaw_rec.append(vehicle_state.yaw)

        plt.cla()
        plt.plot(x, y, color='gray', linewidth=2.0)
        plt.plot(x_rec, y_rec, linewidth=2.0, color='darkviolet')
        # draw.draw_car(x_rec[-1], y_rec[-1], yaw[-1], steer, C)
        plt.axis("equal")
        plt.title("LQR & PID: v=" + str(vehicle_state.v * 3.6)[:4] + "km/h")
        plt.gcf().canvas.mpl_connect('key_release_event',
                                     lambda event:
                                     [exit(0) if event.key == 'escape' else None])
        plt.pause(0.001)

        if dist < 0.3 and abs(vehicle_state.v) < 5.0:
            break

    plt.show()


if __name__ == '__main__':
    main()