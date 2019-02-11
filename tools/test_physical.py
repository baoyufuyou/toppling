import rospy
import logging
import argparse
import numpy as np
import os
import random

from autolab_core import Point, RigidTransform, YamlConfig, TensorDataset
from dexnet.constants import *
from dexnet.envs import GraspingEnv
from dexnet.visualization import DexNetVisualizer3D as vis3d
from ambidex.databases.postgres import YamlLoader, PostgresSchema
from ambidex.class_registry import postgres_base_cls_map, full_cls_list
from toppling.policies import TopplingPolicy
from toppling import is_equivalent_pose

SEED = 107
CAMERA_ROT = np.array([[ 0,-1, 0],
                       [-1, 0, 0],
                       [ 0, 0,-1]])
theta = np.pi/3
c = np.cos(theta)
s = np.sin(theta)
CAMERA_ROT = np.array([[c,0,-s],
                       [0,1,0],
                       [s,0,c]]).dot(CAMERA_ROT)
theta = np.pi/6
c = np.cos(theta)
s = np.sin(theta)
CAMERA_TRANS = np.array([-.25,-.25,.35])
CAMERA_TRANS = np.array([-.4,0,.3])
CAMERA_POSE = RigidTransform(CAMERA_ROT, CAMERA_TRANS, from_frame='camera', to_frame='world')

class YamlObjLoader(object):
    def __init__(self, basedir):
        self.basedir = basedir
        self._map = {}
        for root, dirs, fns in os.walk(basedir):
            for fn in fns:
                full_fn = os.path.join(root, fn)
                _, f = os.path.split(full_fn)
                if f in self._map:
                    raise ValueError('Duplicate file named {}'.format(f))
                self._map[f] = full_fn
        self._yaml_loader = YamlLoader(PostgresSchema('pg_schema', postgres_base_cls_map, full_cls_list))

    def load(self, key):
        key = key + '.yaml'
        full_filepath = self._map[key]
        return self._yaml_loader.load(full_filepath)

    def clear(self):
        self._yaml_loader.clear()

    def __call__(self, key):
        return self.load(key)


def kill_stream():
    subprocess.call('killall vlc', shell=True)

def update_stream(mask_file=None):
    kill_stream()
    time.sleep(0.5)
    if mask_file is not None:
        subprocess.call('cvlc v4l2:///dev/video0:width=1280:height=960 --sub-filter=logo --logo-file={} --logo-opacity=200 \
                        --logo-position=5 &'.format(mask_file), shell=True)
    else:
        subprocess.call('cvlc v4l2:///dev/video0 &', shell=True)
    time.sleep(1)

# Used to reset the camera on the fly (sometimes it randomly crashes)
def reset_stream_usb():
    dev = finddev(idVendor=0x046d, idProduct=0x081b)
    logging.info('Resetting {}'.format(dev._str()))
    try:
        dev.reset()
        logging.info('Reset Successful')
    except:
        logging.warn('Failed to find device!')

def create_scene(camera, workspace_objects):

    # Start with an empty scene
    scene = Scene()

    # Create a VirtualCamera
    virt_cam = VirtualCamera(camera.intrinsics, camera.pose)

    # Add the camera to the scene
    scene.camera = virt_cam
    mp = MaterialProperties(
            color=np.array([0.3,0.3,0.3]),
            k_a=0.5, k_d=0.3, k_s=0.0, alpha=10.0
    )
    if camera.geometry is not None:
        so = SceneObject(camera.geometry, camera.pose.copy(), mp)
        scene.add_object(camera.name, so)

    return scene

def update_scene(scene, grasp_obj):
    
    # Remove old objects
    scene_objs = scene.objects.copy()
    if 'obj' in scene_objs:
        scene.remove_object('obj')
    
    # Get graspable object material properties and add to scene
    mp = hasattr(grasp_obj, 'material_properties')
    if not mp:
        mp = MaterialProperties(
            color=np.random.uniform(0.0, 1.0, size=3),
            k_a=0.5, k_d=0.3, k_s=0.0, alpha=10.0
        )
    so = SceneObject(grasp_obj.geometry, grasp_obj.pose.copy(), mp)
    scene.add_object(grasp_obj.name, so)

def parse_args():
    default_config_filename = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                       '..',
                                       'cfg/tools/benchmark_topple_policy_graspingenv.yaml'
    )
    parser = argparse.ArgumentParser(description='Rollout a policy for bin picking in order to evaluate performance')
    parser.add_argument('--config_filename', type=str, default=default_config_filename, help='configuration file to use')
    return parser.parse_args()

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO)
    args = parse_args()

    config = YamlConfig(args.config_filename)
    policy = TopplingPolicy(config['policy'], use_sensitivity=True)

    if config['debug']:
        random.seed(SEED)
        np.random.seed(SEED)

    # Start webcam stream and get webcam tf
    webcam_tf = RigidTransform.load(os.path.join('/nfs/diskstation/calib', 'webcam', 'webcam_to_world.tf'))
    phoxi_tf = RigidTransform.load(os.path.join('/nfs/diskstation/calib', 'phoxi', 'phoxi_to_world.tf'))
    bin_tf = RigidTransform(translation=np.array([0.387500, -0.003000, -0.0025]), 
                            rotation=np.array([[-0.024541, -0.999699, 0.01000],
                                               [0.999699, -0.024541, 0.000000],
                                               [0.000000, 0.000000, 1.000000]]), 
                            from_frame='bin', to_frame='world')
    
    # Load all python objects
    basedir = os.path.join(os.path.dirname(__file__), '..', 'tests', 'cfg')
    yaml_obj_loader = YamlObjLoader(basedir)

    phys_robot = yaml_obj_loader('physical_yumi')
    robot = yaml_obj_loader('yumi')
    work_bin = yaml_obj_loader('bin')
    work_bin.pose = bin_tf
    phoxi = yaml_obj_loader('phoxi')
    phoxi.pose = phoxi_tf
    webcam = yaml_obj_loader('webcam')
    webcam.pose = webcam_tf
    
    # Create scene for each camera for rendering sim
    color_scene = create_scene(webcam, [work_bin])
    depth_scene = create_scene(phoxi, [work_bin])

    # Create ICP objects
    feature_matcher = PointToPlaneFeatureMatcher()
    solver = PointToPlaneICPSolver()

    # Start physical depth and color cameras
    update_stream()
    logging.info('Creating Phoxi sensor')
    sensor_config = {'frame': 'phoxi', 'device_name': 1703005, 'size': 'small'}
    phoxi_sensor = RgbdSensorFactory.sensor('phoxi', sensor_config)
    logging.info('Starting Phoxi sensor')
    phoxi_sensor.start()
    logging.info('Phoxi sensor initialized')

    # Create box for filtering point cloud
    min_pt = np.array([0.25, -0.12, 0])
    max_pt = np.array([0.5, 0.12, 0.25])
    mask_box = Box(min_pt, max_pt, 'world')