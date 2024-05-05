import inspect
import logging
import traceback
from dataclasses import dataclass
from typing import Any, List, TypedDict

from devon.environment.agent import Agent
from devon.environment.environment import LocalEnvironment
from devon.environment.prompt import parse_response
from devon.environment.tools import (
    ask_user,
    close_file,
    create_file,
    delete_file,
    edit_file,
    exit,
    extract_signature_and_docstring,
    find_class,
    find_file,
    find_function,
    get_cwd,
    list_dirs_recursive,
    no_op,
    open_file,
    parse_command,
    real_write_diff,
    scroll_down,
    scroll_to_line,
    scroll_up,
    search_dir,
    search_file,
    submit,
)
from devon.environment.utils import DotDict


@dataclass(frozen=False)
class SessionArguments:
    path: str
    environment: str
    user_input: Any


# events
# model thought action pair
#  - generated by agent, consumed by environment
# tool response (observation)
#  - generated by environment and consumed by agent
# tool user request (user input)
# - generated by user and consumed by agent
# interrupt
#  - generated by user or tool and consumed by agent
# stop
#  - generated by user or tool and consumed by agent
# task
#  - generated by user or tool or agent and consumed by agent

# event log can be mapped to an agent chat history


class Event(TypedDict):
    type: str  # types: ModelResponse, ToolResponse, UserRequest, Interrupt, Stop
    content: str
    identifier: str | None
    user_input: Any


class Session:
    def __init__(self, args: SessionArguments, agent):
        logger = logging.getLogger(__name__)

        self.state = DotDict({})
        self.state.PAGE_SIZE = 200
        self.logger = logger
        self.agent = agent
        self.base_path = args.path
        self.event_log: List[Event] = []
        self.event_index = 0
        self.get_user_input = args.user_input

        self.state.editor = {}

        self.path = args.path
        self.environment_type = args.environment

        if args.environment == "local":
            self.environment = LocalEnvironment(args.path)
        else:
            raise ValueError("Unknown environment type")

        self.tools = [
            list_dirs_recursive,
            close_file,
            create_file,
            open_file,
            search_dir,
            find_function,
            find_class,
            search_file,
            get_cwd,
            delete_file,
            submit,
            no_op,
            scroll_up,
            scroll_down,
            scroll_to_line,
            find_file,
            ask_user,
            exit,
            edit_file,
        ]

    def to_dict(self):
        return {
            "path": self.path,
            "environment": self.environment_type,
            "event_history": [event for event in self.event_log],
            "state": self.state.to_dict(),
            "cwd": self.environment.get_cwd(),
            "agent": {
                "name": self.agent.name,
                "model": self.agent.model,
                "temperature": self.agent.temperature,
                "chat_history": self.agent.chat_history,
            },
        }

    @classmethod
    def from_dict(cls, data):
        instance = cls(
            args=SessionArguments(
                path=data["path"],
                environment=data["environment"],
                user_input=data["user_input"],
            ),
            agent=Agent(
                name=data["agent"]["name"],
                model=data["agent"]["model"],
                temperature=data["agent"]["temperature"],
                chat_history=data["agent"]["chat_history"],
            ),
        )

        instance.state = DotDict(data["state"])
        instance.event_log = data["event_history"]
        instance.environment.communicate("cd " + data["cwd"])

        return instance

    def step(self, action: str, thought: str) -> tuple[str, bool]:
        # parse command
        # run command/tool
        # return reponse as observation

        if action == "exit":
            return "Exited task", True

        try:
            return self.parse_command_to_function(command_string=action)
        except Exception as e:
            return e.args[0], False

    def step_event(self):
        if self.event_index == len(self.event_log):
            return "No more events to process", True
        event = self.event_log[self.event_index]
        self.logger.info(f"Event: {event}")
        if event["type"] == "ModelResponse":
            thought, action = parse_response(event["content"])

            if "ask_user" in action:
                self.event_log.append(
                    {
                        "type": "UserRequest",
                        "content": action.split("ask_user")[1],
                        "identifier": self.agent.name,
                    }
                )

            elif action.strip() in ["exit", "stop", "submit"]:
                self.event_log.append(
                    {
                        "type": "Stop",
                        "content": "Stopped task",
                        "identifier": self.agent.name,
                    }
                )

            else:
                try:
                    output, done = self.parse_command_to_function(command_string=action)
                except Exception as e:
                    self.logger.error(traceback.print_exc())
                    output = str(e)

                self.event_log.append(
                    {
                        "type": "ToolResponse",
                        "content": output,
                        "identifier": self.environment.__class__.__name__,
                    }
                )

        if event["type"] == "ToolResponse":
            #  get last event of type task
            task = None
            for e in self.event_log[::-1]:
                if e["type"] == "Task":
                    task = e["content"]
                    break
            if task is None:
                task = "Task unspecified ask user to specify task"
            print("OBERVATION", event["content"])
            thought, action, output = self.agent.predict(task, event["content"], self)
            self.event_log.append(
                {
                    "type": "ModelResponse",
                    "content": output,
                    "identifier": self.agent.name,
                }
            )

        if event["type"] == "UserRequest":
            user_input = self.get_user_input()
            if user_input is None:
                self.logger.info("No user input provided")
                self.event_log.append(
                    {
                        "type": "Stop",
                        "content": "No user input provided",
                        "identifier": None,
                    }
                )
                return "No user input provided", True
            self.event_log.append(
                {"type": "ToolResponse", "content": user_input, "identifier": "user"}
            )

        if event["type"] == "Interrupt":
            task = None
            for event in self.event_log[::-1]:
                if event["type"] == "Task":
                    task = event["content"]
                    break
            if task is None:
                task = "Task unspecified ask user to specify task"

            thought, action, output = self.agent.predict(
                task,
                "You have been interrupted, pay attention to this message "
                + event["content"],
                self,
            )
            self.event_log.append(
                {
                    "type": "ModelResponse",
                    "content": output,
                    "identifier": self.agent.name,
                }
            )

        if event["type"] == "Stop":
            return "Stopped task", True

        if event["type"] == "Task":
            task = event["content"]
            self.logger.info(f"Task: {task}")
            if task is None:
                task = "Task unspecified ask user to specify task"

            thought, action, output = self.agent.predict(task, "", self)
            self.event_log.append(
                {
                    "type": "ModelResponse",
                    "content": output,
                    "identifier": self.agent.name,
                }
            )
        self.event_index += 1
        return self.step_event()

    def parse_command_to_function(self, command_string) -> tuple[str, bool]:
        """
        Parses a command string into its function name and arguments.
        """
        ctx = self

        fn_name, args = parse_command(ctx, command_string)
        if fn_name in ["vim", "nano"]:
            return "Interactive Commands are not allowed", False

        if (
            fn_name == "python"
            and len([line for line in command_string.splitlines() if line]) != 1
        ):
            return "Interactive Commands are not allowed", False

        fn_names = [fn.__name__ for fn in self.tools]

        try:
            if fn_name == "edit_file":
                try:
                    return real_write_diff(self, command_string), False
                except Exception as e:
                    ctx.logger.error(traceback.print_exc())
                    raise e
            elif fn_name in fn_names:
                for fn in self.tools:
                    if fn.__name__ == fn_name:
                        return fn(ctx, *args), False
            else:
                try:
                    output, rc = ctx.environment.communicate(
                        fn_name + " " + " ".join(args)
                    )
                    if rc != 0:
                        raise Exception(output)
                    return output, False
                except Exception as e:
                    ctx.logger.error(
                        f"Failed to execute bash command '{fn_name}': {str(e)}"
                    )
                    return "Failed to execute bash command", False
        except Exception as e:
            ctx.logger.error(traceback.print_exc())
            return e.args[0], False

    def get_available_actions(self) -> list[str]:
        return [fn.__name__ for fn in self.tools]

    def generate_command_docs(self):
        """
        Generates a dictionary of function names and their docstrings.
        """

        funcs = self.tools
        docs = {}

        for func in funcs:
            name = func.__name__
            code = inspect.getsource(func)
            sig, docstring = extract_signature_and_docstring(code)
            docs[name] = {"signature": sig, "docstring": docstring}

        return docs

    # start
    def enter(self):
        self.environment.enter()

    def exit(self):
        self.environment.exit()
