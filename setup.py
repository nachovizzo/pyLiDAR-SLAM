from setuptools import find_packages, setup

setup(
    name="pylidar_slam",
    version="0.1",
    author="Pierre Dellenbach and Ignacio Vizzo",
    author_email="ignaciovizzo@gmail.com",
    # Pacakge infromation
    package_dir={"": "src"},
    packages=find_packages("src"),
    entry_points={"console_scripts": ["run_slam = slam.run:main"]},
    include_package_data=True,
)
