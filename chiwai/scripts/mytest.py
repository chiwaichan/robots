from strands import Agent
from strands_robots import Robot, gr00t_inference

# Create robot with cameras
robot = Robot(
    tool_name="my_arm",
    robot="so101_follower",
    cameras={
        "front": {"type": "opencv", "index_or_path": "/dev/video0", "fps": 30},
        "wrist": {"type": "opencv", "index_or_path": "/dev/video2", "fps": 30}
    },
    port="/dev/ttyACM0",
    data_config="so100_dualcam"
)

# Create agent with robot tool
agent = Agent(tools=[robot, gr00t_inference])

# Start GR00T inference service
agent.tool.gr00t_inference(
    action="start",
    checkpoint_path="/data/checkpoints/gr00t-wave/checkpoint-300000",
    port=5555,
    data_config="so100_dualcam"
)

# Control robot with natural language
agent("Use my_arm to pick up the red block using GR00T policy on port 8000")
