# Project Imports
from slam.common.geometry import projection_map_to_points, mask_not_null
from slam.common.pose import Pose
from slam.common.projection import Projector
from slam.odometry.alignment import RigidAlignmentConfig, RIGID_ALIGNMENT, RigidAlignment
from slam.odometry.initialization import InitializationConfig, INITIALIZATION, Initialization
from slam.odometry.odometry import *
from slam.odometry.local_map import LOCAL_MAP, LocalMapConfig, LocalMap
from slam.viz.__debug_utils import *
from slam.viz.color_map import *


# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class ICPFrameToModelConfig(OdometryConfig):
    """
    The Configuration for the Point-To-Plane ICP based Iterative Least Square estimation of the pose
    """
    algorithm: str = "icp_F2M"
    device: str = "cpu"
    pose: str = "euler"
    max_num_alignments: int = 100

    # Config for the Initialization
    initialization: InitializationConfig = MISSING

    # Config for the Local Map
    local_map: LocalMapConfig = MISSING

    # Config for the Rigid Alignment
    alignment: RigidAlignmentConfig = MISSING

    threshold_delta_pose: float = 1.e-4
    threshold_trans: float = 0.1
    threshold_rot: float = 0.3
    sigma: float = 0.1

    # The data key which is used to search into the data dictionary for the pointcloud to register onto the new frame
    data_key: str = "vertex_map"


# ----------------------------------------------------------------------------------------------------------------------
class ICPFrameToModel(OdometryAlgorithm):
    """
    OdometryAlgorithm based on the ICP-registration
    """

    def __init__(self, config: ICPFrameToModelConfig,
                 projector: Projector = None, pose: Pose = Pose("euler"),
                 device: torch.device = torch.device("cpu"), **kwargs):
        OdometryAlgorithm.__init__(self, config)

        assert_debug(projector is not None)
        self.pose = pose
        self.projector = projector
        self.device = device

        # --------------------------------
        # Loads Components from the Config
        self._motion_model: Initialization = INITIALIZATION.load(self.config.initialization,
                                                                 pose=self.pose, device=device)
        self.local_map: LocalMap = LOCAL_MAP.load(self.config.local_map,
                                                  pose=self.pose, projector=projector)

        self.config.alignment.pose = self.pose.pose_type
        self.rigid_alignment: RigidAlignment = RIGID_ALIGNMENT.load(self.config.alignment, pose=self.pose)

        # -----------------------
        # Optimization Parameters
        self.gn_max_iters = self.config.max_num_alignments

        # ---------------------
        # Local state variables
        self.relative_poses: list = []
        self._iter = 0
        self._tgt_vmap: torch.Tensor = None
        self._tgt_pc: torch.Tensor = None
        self._tgt_nmap: torch.Tensor = None
        self._delta_since_map_update = None  # delta pose since last estimate update
        self._register_threshold_trans = self.config.threshold_trans
        self._register_threshold_rot = self.config.threshold_rot

    def init(self):
        """Initialize/ReInitialize the state of the Algorithm and its components"""
        super().init()
        self.relative_poses = []
        self.local_map.init()
        self._motion_model.init()
        self._iter = 0
        self._delta_since_map_update = torch.eye(4, dtype=torch.float32, device=self.device).reshape(1, 4, 4)

    # ------------------------------------------------------------------------------------------------------------------
    def do_process_next_frame(self, data_dict: dict):
        """
        Processes a new frame

        Estimates the motion for the new frame, and update the states of the different components
        (Local Map, Initialization)

        Args:
            data_dict (dict): The input frame to be processed.
                              The key 'self.config.data_key' is required
        """
        # Reads the input frame
        self._read_input(data_dict)

        if self._iter == 0:
            # Initiate the map with the first frame
            relative_pose = torch.eye(4, dtype=torch.float32,
                                      device=self._tgt_vmap.device).unsqueeze(0)
            self.local_map.update(relative_pose,
                                  new_vertex_map=self._tgt_vmap)
            self.relative_poses.append(relative_pose.cpu().numpy())
            self._iter += 1
            return

        # Extract initial estimate
        initial_estimate = self._motion_model.next_initial_pose(data_dict)

        # Registers the new frame onto the map
        new_rpose, losses = self.register_new_frame(self._tgt_vmap[0],
                                                    initial_estimate, data_dict=data_dict)

        # Update initial estimate
        self.update_initialization(new_rpose, data_dict)
        self.__update_map(new_rpose, data_dict)

        # Update Previous pose
        self.relative_poses.append(new_rpose.cpu().numpy())

        self._iter += 1

    def register_new_frame(self,
                           target_vmap: torch.Tensor,
                           initial_estimate: Optional[torch.Tensor] = None,
                           data_dict: Optional[dict] = None,
                           **kwargs) -> (torch.Tensor, torch.Tensor, torch.Tensor):
        """
        Registers a new frame against the Local Map

        Args:
            target_vmap (torch.Tensor): The target Ver
            initial_estimate (Optional[torch.Tensor]): The initial motion estimate for the ICP
            data_dict (dict): The dictionary containing the data of the new frame

        Returns
            pose_matrix (torch.Tensor): The relative pose between the current frame and the map `(1, 4, 4)`

        """
        check_sizes(target_vmap, [3, -1, -1])
        new_pose_matrix = initial_estimate
        if initial_estimate is None:
            new_pose_matrix = torch.eye(4, device=target_vmap.device,
                                        dtype=target_vmap.dtype).unsqueeze(0)

        _, h, w = target_vmap.shape
        losses = []

        old_target_points = projection_map_to_points(target_vmap, dim=0)
        old_target_points = old_target_points[old_target_points.norm(dim=-1) > 0.0]
        for _ in range(self.gn_max_iters):
            target_points = self.pose.apply_transformation(old_target_points.unsqueeze(0), new_pose_matrix)[0]

            # Compute the nearest neighbors for the selected points
            neigh_pc, neigh_normals, tgt_pc = self.local_map.nearest_neighbor_search(target_points)

            # Compute the rigid transform alignment
            delta_pose, residuals = self.rigid_alignment.align(neigh_pc,
                                                               tgt_pc,
                                                               neigh_normals,
                                                               **kwargs)

            loss = residuals.sum()
            losses.append(loss)

            if delta_pose.norm() < self.config.threshold_delta_pose:
                break

            new_pose_matrix = self.pose.build_pose_matrix(delta_pose) @ new_pose_matrix

        return new_pose_matrix, losses

    def get_relative_poses(self) -> np.ndarray:
        """Returns the estimated relative poses for the current sequence"""
        if len(self.relative_poses) == 0:
            return None
        return np.concatenate(self.relative_poses, axis=0)

    def update_initialization(self, new_rpose, data_dict: dict):
        """Send the frame to the initialization after registration for its state update"""
        self._motion_model.register_motion(new_rpose, data_dict)

    # ------------------------------------------------------------------------------------------------------------------
    # `Private` methods

    def _read_input(self, data_dict: dict):
        """Reads and interprets the input from the data_dict"""
        assert_debug(self.config.data_key in data_dict)
        data = data_dict[self.config.data_key]

        self._tgt_vmap = None
        self._tgt_pc = None
        if isinstance(data, np.ndarray):
            check_sizes(data, [-1, 3])
            pc_data = torch.from_numpy(data).to(self.device).unsqueeze(0)
            # Project into a spherical image
            vertex_map = self.projector.build_projection_map(pc_data.unsqueeze(0))
        elif isinstance(data, torch.Tensor):
            if len(data.shape) == 3 or len(data.shape) == 4:
                # Cast the data tensor as a vertex map
                vertex_map = data.to(self.device)
                if len(data.shape) == 3:
                    vertex_map = vertex_map.unsqueeze(0)
                else:
                    assert_debug(data.shape[0] == 1, f"Unexpected batched data format.")
                check_sizes(vertex_map, [1, 3, -1, -1])
                pc_data = vertex_map.permute(0, 2, 3, 1).reshape(1, -1, 3)
                pc_data = pc_data[mask_not_null(pc_data, dim=-1)[:, :, 0]]

            else:
                assert_debug(len(data.shape) == 2)
                pc_data = data.to(self.device)
                vertex_map = self.projector.build_projection_map(pc_data)
        else:
            raise RuntimeError(f"Could not interpret the data: {data} as a pointcloud tensor")

        self._tgt_vmap = vertex_map
        self._tgt_pc = pc_data

    def __update_map(self, new_rpose: torch.Tensor, data_dict: dict):
        # Updates the map if the motion since last registration is large enough
        new_delta = self._delta_since_map_update @ new_rpose
        delta_params = self.pose.from_pose_matrix(new_delta)

        if delta_params[0, :3].norm() > self._register_threshold_trans or \
                delta_params[0, 3:].norm() * 180 / np.pi > self._register_threshold_rot:

            new_mask = mask_not_null(self._tgt_vmap)
            new_nmap = None
            if "normal_map" in data_dict:
                new_nmap = data_dict["normal_map"]
            self.local_map.update(new_rpose,
                                  new_vertex_map=self._tgt_vmap,
                                  new_pc_data=self._tgt_pc,
                                  normal_map=new_nmap,
                                  mask=new_mask)
            self._delta_since_map_update = torch.eye(4, dtype=torch.float32, device=self.device)
        else:
            self.local_map.update(new_rpose)
            self._delta_since_map_update = new_delta
    # ------------------------------------------------------------------------------------------------------------------