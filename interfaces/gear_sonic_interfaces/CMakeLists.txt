cmake_minimum_required(VERSION 3.8)
project(gear_sonic_interfaces)

# ROS Humble's generators and empy are installed for the system interpreter.
# The parent project may otherwise inherit an active application virtualenv.
if(EXISTS "/usr/bin/python3")
  set(Python3_EXECUTABLE "/usr/bin/python3" CACHE FILEPATH
      "Python used by ROS 2 interface generators" FORCE)
  set(PYTHON_EXECUTABLE "/usr/bin/python3" CACHE FILEPATH
      "Legacy Python used by ROS 2 interface generators" FORCE)
endif()

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/SubmitCarryTask.srv"
  DEPENDENCIES geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
