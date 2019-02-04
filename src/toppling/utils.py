import numpy as np

from autolab_core import RigidTransform
from dexnet.core import StablePose

up = np.array([0,0,1])

def normalize(vec, axis=None):
    """
    Returns a normalied version of the passed in array

    Parameters
    ----------
    vec : nx3, :obj:`numpy.ndarray` or 3xn :obj:`numpy.ndarray`
    axis : int
        if vec is nx3, axis describes which axis to normalize over

    Returns
    -------
    :obj:`numpy.ndarray` of shape vec
        normalized version of vec
    """
    return vec / np.linalg.norm(vec) if axis == None else vec / np.linalg.norm(vec, axis=axis).reshape((-1,1))

def stable_pose(R):
    """
    Returns a stable pose object from RigidTransform

    Parameters
    ----------
    R : :obj:`autolab_core.RigidTransform`

    Returns
    -------
    :obj:`StablePose`
    """
    if isinstance(R, RigidTransform):
        R = R.matrix
    return StablePose(0, R, eq_thresh=.03, to_frame='world')

def is_equivalent_pose(R1, R2):
    """
    Returns whether the two RigidTransforms are equivalent stable poses

    Parameters
    ----------
    R1 : :obj:`autolab_core.RigidTransform`
    R2 : :obj:`autolab_core.RigidTransform`

    Returns
    -------
    bool
    """
    z1 = R1.inverse().matrix[:,2]
    z2 = R2.inverse().matrix[:,2]
    diff = np.linalg.norm(z1 - z2)
    return -.1 < diff and diff < .1
