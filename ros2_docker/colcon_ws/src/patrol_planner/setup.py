from setuptools import setup

package_name = 'patrol_planner'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'Pillow', 'requests', 'websocket-client', 'ultralytics'],
    zip_safe=True,
    maintainer='Yeongcheon Patrol Team',
    maintainer_email='david5324@pusan.ac.kr',
    description='영천 빈집 순찰 시스템 (TSP + A*)',
    license='Apache 2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # ros2 run patrol_planner patrol_node 으로 실행
            'patrol_node = patrol_planner.patrol_node:main',
            'yolo_detector_node = patrol_planner.yolo_detector_node:main',
        ],
    },
)
