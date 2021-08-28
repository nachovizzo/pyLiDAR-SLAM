from abc import ABC
from enum import Enum
from typing import Dict, Optional, Any

import torch

# Hydra and OmegaConf
from dataclasses import field, MISSING
from hydra.conf import dataclass

# Project Imports
from hydra.core.config_store import ConfigStore
from omegaconf import OmegaConf

from pylidar_slam.common.optimization import GaussNewton, PointToPlaneCost
from pylidar_slam.common.pose import Pose

# ----------------------------------------------------------------------------------------------------------------------
from pylidar_slam.common.utils import assert_debug


@dataclass
class RigidAlignmentConfig:
    """Configuration for the alignments used by ICP methods"""
    mode: str = MISSING

    pose: str = "euler"  # The pose representation
    scheme: str = "huber"  # The weighting scheme for robust alignment


class RigidAlignment(ABC):
    """
    Abstract class for rigid 3D alignment between Point Clouds

    A Rigid alignment is the estimation of the rigid transformation between a set of 3D correspondences
    """

    def __init__(self, alignment_config: RigidAlignmentConfig, **kwargs):
        self.config = alignment_config
        self.pose: Pose = Pose(self.config.pose)
        self.point_to_plane: PointToPlaneCost = PointToPlaneCost(pose=self.pose)

    def align(self,
              ref_points: torch.Tensor,
              tgt_points: torch.Tensor, *args, **kwargs) -> [torch.Tensor,
                                                             torch.Tensor]:
        """
        Aligns corresponding pair of 3D points

        Computes the optimal rigid transform between
        reference points (`ref_points`) and target points (`tgt_points`).

        Args:

            ref_points (torch.Tensor): Reference points `(B, N, 3)`
            tgt_points (torch.Tensor): Target points `(B, N, 3)`
            *args: Other arguments for child classes
            **kwargs: Other named arguments required by child classes

        Returns:
            A tuple consisting of the pose matrix of the estimated transform,
            And the residual errors between the target and the reference
        """
        raise NotImplementedError("")


# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class GaussNewtonPointToPlaneConfig(RigidAlignmentConfig):
    """Configuration for a Gauss-Newton based Point-to-Plane rigid alignment"""

    mode = "point_to_plane_gauss_newton"
    num_gn_iters: int = 1

    # The configuration for the Gauss-Newton algorithm
    gauss_newton_config: Dict[str, Any] = field(default_factory=lambda: dict(max_iters=1))


class GaussNewtonPointToPlaneAlignment(RigidAlignment):
    """
    A GaussNewton Point-To-Plane rigid alignment method,
    Which minimizes the Point-To-Plane distance
    """

    def __init__(self, config: GaussNewtonPointToPlaneConfig, **kwargs):
        super().__init__(config, **kwargs)

        self.gauss_newton: GaussNewton = GaussNewton(**self.config.gauss_newton_config)

    def align(self,
              ref_points: torch.Tensor,
              tgt_points: torch.Tensor,
              ref_normals: torch.Tensor,
              initial_estimate: Optional[torch.Tensor] = None,
              mask: Optional[torch.Tensor] = None, **kwargs) -> [torch.Tensor,
                                                                 torch.Tensor]:
        """
        Aligns the target Point Cloud on the reference Point Cloud

        Args:
            ref_points (torch.Tensor): The reference points tensor `(B, N, 3)`
            tgt_points (torch.Tensor): The target points tensor `(B, N, 3)`
            ref_normals (torch.Tensor): The reference normals tensor `(B, N, 3)`
            initial_estimate (torch.Tensor): An initial transform `(B, D)`
                                             Where `D` is the number of parameters of the pose representation
            mask (torch.Tensor): An optional mask applied on the input points to remove some from the computation
        """
        if initial_estimate is None:
            initial_estimate = torch.zeros(ref_points.shape[0], self.pose.num_params(),
                                           dtype=ref_points.dtype,
                                           device=ref_points.device)
        elif len(initial_estimate.shape) == 3:
            initial_estimate = self.pose.from_pose_matrix(initial_estimate)

        res_func = PointToPlaneCost.get_residual_fun(tgt_points, ref_points,
                                                     ref_normals, pose=self.pose, mask=mask,
                                                     **kwargs)
        jac_func = PointToPlaneCost.get_residual_jac_fun(tgt_points, ref_points,
                                                         ref_normals, pose=self.pose, mask=mask, **kwargs)
        new_pose_params, residuals = self.gauss_newton.compute(initial_estimate, res_func, jac_func,
                                                               # Pass the pointclouds for some weighting schemes
                                                               target_points=tgt_points, reference_points=ref_points,
                                                               **kwargs)

        return new_pose_params, residuals


# ----------------------------------------------------------------------------------------------------------------------
# Hydra Group odometry/local_map definition
cs = ConfigStore.instance()
cs.store(group="slam/odometry/alignment", name="point_to_plane_GN", node=GaussNewtonPointToPlaneConfig)


# ----------------------------------------------------------------------------------------------------------------------
class RIGID_ALIGNMENT(Enum):
    """A Convenient Enum to load the"""
    point_to_plane_gauss_newton = (GaussNewtonPointToPlaneAlignment, GaussNewtonPointToPlaneConfig)

    # TODO : point_to_plane_linear

    @staticmethod
    def load(config: RigidAlignmentConfig, **kwargs) -> RigidAlignment:
        """Returns a RigidAlignment instance loaded from the config"""
        assert_debug(config.mode in RIGID_ALIGNMENT.__members__)
        _class, _config = RIGID_ALIGNMENT.__members__[config.mode].value
        return _class(_config(**config))
