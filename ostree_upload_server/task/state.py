class TaskState(object):
    TASK_STATES = [
        'PENDING',
        'PROCESSING',
        'COMPLETED',
        'FAILED'
    ]

    @staticmethod
    def name(state):
        """Return the name of the state"""
        return TaskState.TASK_STATES[state]


# Dynamically enumerate all the options as consts on the class
for index, task in enumerate(TaskState.TASK_STATES):
    if not getattr(TaskState, task, None):
        setattr(TaskState, task, index)
