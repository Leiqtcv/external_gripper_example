#!/usr/bin/env python

import actionlib_msgs.msg
import franka_gripper.msg
import franka_control.srv
import geometry_msgs.msg
from robotiq_2f_gripper_msgs.msg import CommandRobotiqGripperAction, CommandRobotiqGripperGoal, CommandRobotiqGripperResult 
import controller_manager_msgs.srv
import moveit_commander
import actionlib
import rospy
import rospkg
import rosparam
import os.path
import sys


class Context:
    def __init__(self, move_group_name):
        rospy.loginfo('Waiting for move_group/status')
        rospy.wait_for_message('move_group/status',
                               actionlib_msgs.msg.GoalStatusArray)

        self.commander = moveit_commander.MoveGroupCommander(move_group_name)

        rospy.loginfo('Waiting for robotiq_gripper/command_robotiq_action')
        self.gripper_move = actionlib.SimpleActionClient(
            '/robotiq_gripper/command_robotiq_action',
            CommandRobotiqGripperAction)
        self.gripper_move.wait_for_server()

        rospy.loginfo('Waiting for controller_manager/switch_controller')
        self.switch_controller = rospy.ServiceProxy(
            'controller_manager/switch_controller',
            controller_manager_msgs.srv.SwitchController)
        self.switch_controller.wait_for_service()

        rospy.loginfo('Waiting for '
                      'franka_control/set_force_torque_collision_behavior')
        self.set_collision_behavior = rospy.ServiceProxy(
            'franka_control/set_force_torque_collision_behavior',
            franka_control.srv.SetForceTorqueCollisionBehavior)
        self.set_collision_behavior.wait_for_service()

        self.active_controllers = []

    def load_controllers(self, controllers):
        params = controller_manager_msgs.srv.SwitchControllerRequest()
        params.start_controllers = controllers
        params.stop_controllers = self.active_controllers
        params.strictness = params.STRICT
        if not self.switch_controller(params):
            rospy.logerr("Couldn't switch controllers")
            sys.exit(1)
        self.active_controllers = controllers


def moveit_joint(ctx, position, acc=0.1, vel=0.1):
    ctx.load_controllers(['position_joint_trajectory_controller'])

    print('Moving to joint position {}'.format(position))
    ctx.commander.set_max_acceleration_scaling_factor(acc)
    ctx.commander.set_max_velocity_scaling_factor(vel)
    ctx.commander.go(position, wait=True)
    ctx.commander.stop()


def moveit_cart(ctx, pos, rot, acc=0.1, vel=0.1):
    ctx.load_controllers(['position_joint_trajectory_controller'])

    print('Moving to Cartesian pose: pos {}, rot {}'.format(pos, rot))
    pose_goal = geometry_msgs.msg.Pose()
    pose_goal.orientation.w = rot[0]
    pose_goal.orientation.x = rot[1]
    pose_goal.orientation.y = rot[2]
    pose_goal.orientation.z = rot[3]
    pose_goal.position.x = pos[0]
    pose_goal.position.y = pos[1]
    pose_goal.position.z = pos[2]
    ctx.commander.set_max_acceleration_scaling_factor(acc)
    ctx.commander.set_max_velocity_scaling_factor(vel)
    ctx.commander.set_end_effector_link('panda_link8')
    ctx.commander.set_pose_target(pose_goal)
    ctx.commander.go(wait=True)
    ctx.commander.stop()
    ctx.commander.clear_pose_targets()


def gripper_move(ctx, position, speed, force):
    goal = CommandRobotiqGripperGoal(position=position, speed=speed, force=force)
    rospy.loginfo('Moving gripper:\n{}'.format(goal))
    ctx.gripper_move.send_goal(goal)
    ctx.gripper_move.wait_for_result()
    result = ctx.gripper_move.get_result()
    if not abs(result.requested_position - result.position) < 0.01 :
        rospy.logerr("Couldn't move gripper")
        sys.exit(1)


def set_collision_behavior(ctx, torques, forces):
    rospy.loginfo(
        'Setting CB:\n torques: {}\nforces: {}'.format(torques, forces))
    ctx.set_collision_behavior(lower_torque_thresholds_nominal=torques,
                               upper_torque_thresholds_nominal=torques,
                               lower_force_thresholds_nominal=forces,
                               upper_force_thresholds_nominal=forces)


STEPS = {
  'moveit_cart': moveit_cart,
  'moveit_joint': moveit_joint,
  'gripper_move': gripper_move,
  'set_collision_behavior': set_collision_behavior,
}


def create_step(t, params):
    callback = STEPS[t]
    return lambda ctx: callback(ctx, **params)


if __name__ == '__main__':
    moveit_commander.roscpp_initialize(sys.argv)
    rospy.init_node('demo')

    rospy.loginfo('Waiting for controller_manager/load_controller')
    load_controller = rospy.ServiceProxy(
                 'controller_manager/load_controller',
                 controller_manager_msgs.srv.LoadController)
    load_controller.wait_for_service()

    for controller_name in rospy.get_param('~controllers'):
        if not load_controller(controller_name):
            rospy.logerr('Could not load {}', controller_name)
            sys.exit(1)
    rospy.loginfo('Loaded controllers')

    rospack = rospkg.RosPack()

    ctx = Context('panda_arm')

    demo_files = [os.path.join(rospack.get_path('icra19'),
                               'config',
                               '{}.yaml'.format(f))
                  for f in rospy.get_param('~demos')]
    configs = [rosparam.load_file(f)[0][0] for f in demo_files]
    steps = [create_step(x['type'], x['params']) for x in sum(configs, [])]

    rospy.loginfo('Running steps')
    for step in steps:
        if rospy.is_shutdown():
            sys.exit(0)
        step(ctx)

    rospy.loginfo('Performed all steps, shutting down')
