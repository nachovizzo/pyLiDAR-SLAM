from enum import Enum

from pylidar_slam.common.utils import ObjectLoaderEnum
from pylidar_slam.dataset.configuration import DatasetLoader, DatasetConfig
from pylidar_slam.dataset.kitti_dataset import KITTIDatasetLoader, KITTIConfig
from pylidar_slam.dataset.nclt_dataset import NCLTDatasetLoader, NCLTConfig
from pylidar_slam.dataset.ford_dataset import FordCampusDatasetLoader, FordCampusConfig
from pylidar_slam.dataset.nhcd_dataset import NHCDDatasetLoader, NHCDConfig
from pylidar_slam.dataset.kitti_360_dataset import (KITTI360Config, KITTI360DatasetLoader)

from pylidar_slam.dataset.rosbag_dataset import _with_rosbag


class DATASET(ObjectLoaderEnum, Enum):
    """
    The different datasets covered by the dataset_config configuration
    A configuration must have the field dataset_config pointing to one of these keys
    """
    kitti = (KITTIDatasetLoader, KITTIConfig)
    kitti_360 = (KITTI360DatasetLoader, KITTI360Config)
    nclt = (NCLTDatasetLoader, NCLTConfig)
    ford_campus = (FordCampusDatasetLoader, FordCampusConfig)
    nhcd = (NHCDDatasetLoader, NHCDConfig)
    if _with_rosbag:
        from pylidar_slam.dataset.rosbag_dataset import RosbagDatasetConfiguration, RosbagConfig
        from pylidar_slam.dataset.urban_loco_dataset import UrbanLocoConfig, UrbanLocoDatasetLoader
        rosbag = (RosbagDatasetConfiguration, RosbagConfig)
        urban_loco = (UrbanLocoDatasetLoader, UrbanLocoConfig)

    @classmethod
    def type_name(cls):
        return "dataset"
