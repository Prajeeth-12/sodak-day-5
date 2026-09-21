"""Autonomous LangChain Student Academic Advisor Agent using Google Gemini & SQLite.

Uses LangChain Tools and a Tool-Calling Agent to dynamically select tools for:
- get_student_info(student_id)
- get_student_marks(student_id)
- calculator(expression)
- get_passing_rules()
"""
import os
import sys
import sqlite3
import ast
import operator
from pathlib import Path
from dotenv import load_dotenv

# UTF-8 encoding support for consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Optimize socket resolution on Windows (avoid 20s IPv6 timeouts)
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in res if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else res
socket.getaddrinfo = _getaddrinfo_ipv4

load_dotenv()

from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_google_genai import ChatGoogleGenerativeAI

DB_PATH = Path(__file__).resolve().parent / "students.db"


# ---------------------------------------------------------------------------
# 1. Database Initialization
# ---------------------------------------------------------------------------
def ensure_db():
    from init_db import init_db
    init_db(DB_PATH)


# ---------------------------------------------------------------------------
# 2. Tool Definitions
# ---------------------------------------------------------------------------
@tool
def get_student_info(student_id: str) -> dict:
    """Returns student name and department given their student_id.
    
    Args:
        student_id: The unique student identifier (e.g. '22CS045').
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT name, department FROM students WHERE student_id = ?", (student_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"student_id": student_id, "name": row[0], "department": row[1]}
    return {"error": f"Student with ID '{student_id}' not found."}


@tool
def get_student_marks(student_id: str) -> dict:
    """Returns python mark, database mark, ai mark, and web mark for a student.
    
    Args:
        student_id: The unique student identifier (e.g. '22CS045').
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT python, database, ai, web FROM students WHERE student_id = ?", (student_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "student_id": student_id,
            "python": row[0],
            "database": row[1],
            "ai": row[2],
            "web": row[3],
        }
    return {"error": f"Student with ID '{student_id}' not found."}


@tool
def calculator(expression: str) -> str:
    """Used to calculate total marks and average marks from an arithmetic expression.
    
    Examples:
        - Total: '85 + 72 + 90 + 78'
        - Average: '325 / 4' or '(85 + 72 + 90 + 78) / 4'
    """
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
    }

    def eval_expr(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp):
            return operators[type(node.op)](eval_expr(node.left), eval_expr(node.right))
        elif isinstance(node, ast.UnaryOp):
            return operators[type(node.op)](eval_expr(node.operand))
        raise ValueError(f"Unsupported syntax in expression: {node}")

    try:
        clean_expr = expression.strip().replace("x", "*").replace("X", "*")
        node = ast.parse(clean_expr, mode="eval").body
        result = eval_expr(node)
        return str(round(result, 2) if isinstance(result, float) else result)
    except Exception as e:
        return f"Calculation Error: {e}"


@tool
def get_passing_rules() -> dict:
    """Returns the university passing rules:
    - Minimum overall average: 40%
    - Minimum mark in each subject: 35%
    """
    return {
        "minimum_overall_average_percent": 40,
        "minimum_mark_per_subject": 35,
        "rule_summary": "Student passes if every subject mark >= 35 and overall average >= 40%."
    }


ALL_TOOLS = [get_student_info, get_student_marks, calculator, get_passing_rules]


# ---------------------------------------------------------------------------
# 3. Agent Factory (Google Gemini Only)
# ---------------------------------------------------------------------------
def create_academic_agent(api_key: str = None, model_name: str = "gemini-3.1-flash-lite", verbose: bool = True) -> AgentExecutor:
    """Creates a LangChain Tool-Calling Agent using Google Gemini."""
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not found! Please set GEMINI_API_KEY in your environment, "
            ".env file, or pass it directly."
        )

    llm = ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=key,
        temperature=0.0,
    )

    system_prompt = (
        "You are a helpful university academic advisor assistant.\n"
        "Answer questions accurately by selecting and invoking the appropriate tools:\n"
        "- get_student_info(student_id): Use when asked for student's name or department.\n"
        "- get_student_marks(student_id): Use when asked for subject marks (python, database, ai, web).\n"
        "- calculator(expression): ALWAYS use this for arithmetic (e.g. summing marks, calculating average mark).\n"
        "- get_passing_rules(): Use when asked about pass/fail criteria or university rules.\n\n"
        "Guidelines:\n"
        "1. Never guess or do math in your head; invoke the calculator tool.\n"
        "2. An autonomous agent decides which tools to call dynamically based on the question.\n"
        "3. Provide a clear, polite, and well-structured final answer."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, ALL_TOOLS, prompt)
    return AgentExecutor(
        agent=agent,
        tools=ALL_TOOLS,
        verbose=verbose,
        return_intermediate_steps=True,
        max_iterations=10,
    )


# ---------------------------------------------------------------------------
# 4. Runner
# ---------------------------------------------------------------------------
def run_query(executor: AgentExecutor, query: str) -> None:
    print("\n" + "=" * 75, flush=True)
    print(f"QUERY: {query}", flush=True)
    print("=" * 75, flush=True)
    result = executor.invoke({"input": query})
    print("\n[FINAL ANSWER]:\n", result["output"], flush=True)


if __name__ == "__main__":
    ensure_db()
    agent = create_academic_agent(verbose=True)

    test_queries = [
        "What is the name and department of student 22CS045?",
        "What are the marks of 22CS047?",
        "What is the total and average mark of 22CS045?",
        "Is 22CS045 eligible to pass according to the university rules?",
        "I am 22CS045. Tell me my name, department, total marks, average marks, and whether I satisfy the university passing requirements."
    ]

    for q in test_queries:
        run_query(agent, q)
