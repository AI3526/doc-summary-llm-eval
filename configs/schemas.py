from typing import List, Optional, Literal
from pydantic import BaseModel, Field

from configs.prompt_templates import REFUSAL_TEMPLATE


# ==========================================
# 1. LLM 구조화 응답 검증용 Pydantic Schema
# ==========================================

class ToolCallRequest(BaseModel):
    """모델이 호출하고자 하는 Tool 이름과 인자 구조체"""
    tool_name: str = Field(
        ..., 
        description="호출할 툴의 이름 (send_slack_notification, create_jira_ticket, create_calendar_event 중 선택)"
    )
    arguments: dict = Field(
        default_factory=dict, 
        description="툴 호출에 필요한 인자(Arguments) 딕셔너리"
    )

# 사전 정의된 카테고리 후보군
CategoryType = Literal["복지", "인사", "개발", "경영지원", "보안", "인프라"]

class SummaryResponse(BaseModel):
    """LLM이 최종 제출해야 하는 요약 및 툴 호출 응답 스키마"""
    summary_bullets: List[str] = Field(
        ..., 
        min_items=1,
        max_items=3,
        description=f"본문 내용을 바탕으로 작성한 3줄 불릿포인트 요약 목록 (질문과 상관없는 문서면 ['{REFUSAL_TEMPLATE}'] 1줄만 작성.)"
    )
    categories: List[CategoryType] = Field(
        ...,
        min_items=1,
        max_items=2,
        description="문서 내용과 가장 관련 깊은 카테고리 1~2개 선택 (예: ['개발', '보안'])"
    )
    is_uncertain: bool = Field(
        ..., 
        description="본문 내용이 확정되지 않은 안/논의 단계이거나, 사용자 질문이 문서 내용과 전혀 무관한 경우 반드시 True로 설정"
    )
    tool_calls: Optional[List[ToolCallRequest]] = Field(
        default=None, 
        description="질문 이행을 위해 필요한 Tool Call 목록 (호출할 툴이 없으면 null 또는 빈 리스트)"
    )

RESPONSE_SCHEMA = SummaryResponse.model_json_schema()
# ==========================================
# 2. Native Tool Calling용 JSON Schema (OpenAI API Compatible)
# ==========================================

SLACK_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_slack_notification",
        "description": "슬랙(Slack) 특정 채널에 긴급 공지 또는 업무 알림 메시지를 전송합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "알림을 보낼 슬랙 채널명 (예: #sec-alert, #team-notice)"
                },
                "title": {
                    "type": "string",
                    "description": "알림 메시지의 제목"
                },
                "urgency": {
                    "type": "string",
                    "enum": ["LOW", "HIGH"],
                    "description": "알림 긴급도 수준"
                }
            },
            "required": ["channel", "title", "urgency"]
        }
    }
}

JIRA_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_jira_ticket",
        "description": "Jira 프로젝트에 신규 버그 또는 작업 이슈 티켓을 생성합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {
                    "type": "string",
                    "description": "Jira 프로젝트 키 (예: SEC, DEV, INFRA)"
                },
                "issue_type": {
                    "type": "string",
                    "enum": ["Bug", "Task", "Sub-task", "Epic"],
                    "description": "생성할 이슈의 유형"
                },
                "summary": {
                    "type": "string",
                    "description": "Jira 티켓 요약/제목"
                },
                "priority": {
                    "type": "string",
                    "enum": ["Lowest", "Low", "Medium", "High", "Highest"],
                    "description": "티켓 우선순위"
                }
            },
            "required": ["project_key", "issue_type", "summary", "priority"]
        }
    }
}

CALENDAR_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_calendar_event",
        "description": "구글 캘린더에 정기 점검, 회의 등 일정을 등록합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "캘린더 일정 제목"
                },
                "start_time": {
                    "type": "string",
                    "description": "일정 시작 일시 (ISO 8601 형식, 예: 2026-09-19T22:00:00)"
                },
                "end_time": {
                    "type": "string",
                    "description": "일정 종료 일시 (ISO 8601 형식, 예: 2026-09-20T04:00:00)"
                },
                "description": {
                    "type": "string",
                    "description": "일정 상세 설명 및 중단 대상 서비스 정보"
                }
            },
            "required": ["title", "start_time", "end_time"]
        }
    }
}

# 벤치마크 평가 시 모델에 함께 전달할 툴 세트 전체 목록
BENCHMARK_TOOLS = [
    SLACK_TOOL_SCHEMA,
    JIRA_TOOL_SCHEMA,
    CALENDAR_TOOL_SCHEMA
]

# ==========================================
# 3. Native Tool Calling 실험용 스키마 (scripts/04_toolcalling_local.py, 05_toolcalling_cloud.py 전용)
# ==========================================
# 그래이드 파이프라인(SummaryResponse)은 tool_calls를 응답 필드로 함께 강제하는 "시뮬레이션" 방식이라
# tool_calls를 포함하지만, native tool calling은 Turn A(실제 tools= 호출)가 tool_calls를 이미 담당하므로
# Turn B 최종 응답 스키마(FinalAnswerSchema)에는 tool_calls를 넣지 않는다. 과제 채점과는 무관한 개인 실험용.

class FinalAnswerSchema(BaseModel):
    """native tool calling Turn B(최종 응답) 전용 스키마"""
    summary_bullets: List[str] = Field(
        ...,
        min_items=1,
        max_items=3,
        description=f"본문 내용을 바탕으로 작성한 3줄 불릿포인트 요약 목록 (질문과 상관없는 문서면 ['{REFUSAL_TEMPLATE}'] 1줄만 작성.)"
    )
    categories: List[CategoryType] = Field(
        ...,
        min_items=1,
        max_items=2,
        description="문서 내용과 가장 관련 깊은 카테고리 1~2개 선택 (예: ['개발', '보안'])"
    )
    is_uncertain: bool = Field(
        ...,
        description="본문 내용이 확정되지 않은 안/논의 단계이거나, 사용자 질문이 문서 내용과 전혀 무관한 경우 반드시 True로 설정"
    )

FINAL_ANSWER_JSON_SCHEMA = FinalAnswerSchema.model_json_schema()


class ToolNeedSchema(BaseModel):
    """라우팅 게이트 전용 스키마 — "tool이 필요한 상황인가"만 판단"""
    tool_needed: bool

TOOL_NEED_JSON_SCHEMA = ToolNeedSchema.model_json_schema()