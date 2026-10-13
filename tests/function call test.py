from openai import OpenAI
from datetime import datetime
import json
import os
import random

from backend.config import (
    AGENT_CONTEXT_SAFETY_TOKENS,
    AGENT_MAX_INPUT_TOKENS,
    AGENT_RESERVED_OUTPUT_TOKENS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_REQUEST_TIMEOUT_SECONDS,
)
# 初始化OpenAI客户端
client = OpenAI(
    # 如果没有配置环境变量，请用阿里云百炼API Key替换：api_key="sk-xxx"
    # 各地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    api_key=LLM_API_KEY,
    # 以下为华北2（北京）地域的配置，调用时请将{WorkspaceId}替换为真实的业务空间ID，各地域的配置不同。
    base_url=LLM_BASE_URL,
)

# # 模拟用户问题
# USER_QUESTION = "我所处的地方天气怎么样？"
# # 定义工具列表
# tools = [
#   {
#     "type": "function",
#     "function": {
#       "name": "get_current_weather",
#       "description": "当你需要查询某个城市的天气时使用。",
#       "parameters": {
#         "type": "object",
#         "properties": {
#           "location": {
#             "type": "string",
#             "description": "城市名称，如北京、纽约等。代表当前用户本人所在城市时填 @self，由服务端解析",
#           }
#         },
#         "required": ["location"],
#       },
#     },
#   },
# ]
#
#
# # 模拟天气查询工具
# def get_current_weather(arguments):
#   weather_conditions = ["sunny", "cloudy", "rainy"]
#   random_weather = random.choice(weather_conditions)
#   location = arguments["location"]
#   return f"{location} is {random_weather} today."
#
#
# # 封装模型调用函数
# def get_response(messages):
#   completion = client.chat.completions.create(
#     model="qwen3.8-max",
#     messages=messages,
#     tools=tools,
#   )
#   return completion
#
#
# messages = [{"role": "user", "content": USER_QUESTION}]
# response = get_response(messages)
# assistant_output = response.choices[0].message
#
# print(assistant_output)
# if assistant_output.content is None:
#   assistant_output.content = ""
# messages.append(assistant_output)
# # 如果不需要调用工具，直接输出回复
# if assistant_output.tool_calls is None:
#   print(f"无需调用天气工具。直接回复：{assistant_output.content}")
# else:
#   # 进入工具调用循环
#   while assistant_output.tool_calls is not None:
#     # 遍历本轮返回的全部工具调用，避免只处理第一个而遗漏其余工具的返回结果
#     for tool_call in assistant_output.tool_calls:
#       tool_call_id = tool_call.id
#       func_name = tool_call.function.name
#       arguments = json.loads(tool_call.function.arguments)
#       print(f"调用工具 [{func_name}]，参数：{arguments}")
#       # 执行工具
#       tool_result = get_current_weather(arguments)
#       # 构建工具响应消息
#       tool_message = {
#         "role": "tool",
#         "tool_call_id": tool_call_id,
#         "content": tool_result,
#       }
#       print(f"工具返回结果：{tool_message['content']}")
#       messages.append(tool_message)
#     # 再次调用模型，获取自然语言回复
#     response = get_response(messages)
#     assistant_output = response.choices[0].message
#     if assistant_output.content is None:
#       assistant_output.content = ""
#     messages.append(assistant_output)
#   print(f"最终回复：{assistant_output.content}")



from openai import OpenAI

from backend.agent.contracts import ToolPlan
from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


def request_tool_plan(
    planning_prompt: str,
    messages: list[dict],
) -> ToolPlan:
    # planning_prompt 继续使用项目现有的规划提示词：
    # 包含当前目标允许的业务工具、参数说明和依赖规则。
    tools = [
        {
            "type": "function",
            "function": {
                "name": "submit_tool_plan",
                "description": "提交当前业务目标的工具执行计划",
                # Pydantic 模型转换成模型可理解的参数结构。
                "parameters": ToolPlan.model_json_schema(),

                # 当前 ToolPlan 中 args 是开放字典，
                # 不能直接假设其满足 OpenAI 严格模式要求。
                "strict": False,
            },
        }
    ]

    with OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        max_retries=0,
    ) as client:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": planning_prompt},
                *messages,
            ],
            tools=tools,
            # 要求返回“提交计划”的调用，而非让模型自由选择。
            tool_choice={
                "type": "function",
                "function": {"name": "submit_tool_plan"},
            },
            # 外层只需要一个提交计划的调用。
            parallel_tool_calls=False,
        )

    choice = response.choices[0]
    if choice.finish_reason in {"length", "content_filter"}:
        raise ValueError("模型输出被截断或拦截，不能使用该计划")

    calls = choice.message.tool_calls or []
    if len(calls) != 1:
        raise ValueError("没有收到唯一的计划调用")

    call = calls[0]
    if call.function.name != "submit_tool_plan":
        raise ValueError("模型返回了错误的函数")

    # arguments 是 JSON 字符串，不是 Python 字典。
    # 解析的同时，用当前项目的 Pydantic 契约校验。
    return ToolPlan.model_validate_json(call.function.arguments)