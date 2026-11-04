"""A fixed ledger and the questions it answers (shared with scripts/scrooge_eval.py)."""

from datetime import timedelta

from app.models.models import Agent, CostEvent
from app.timeutils import utcnow

# (agent, model, provider, cost, days ago, status)
_ROWS = [
    ("researcher", "claude-opus-5-5", "anthropic", 10.00, 0, "success"),
    ("researcher", "claude-opus-5-5", "anthropic", 5.00, 1, "success"),
    ("writer", "gpt-6.1-sol", "openai", 2.50, 0, "success"),
    ("writer", "gpt-6.1-sol", "openai", 1.25, 1, "failure"),
    ("triage", "claude-haiku-5-5", "anthropic", 0.75, 0, "success"),
    ("triage", "claude-haiku-5-5", "anthropic", 0.25, 40, "success"),
    ("researcher", "gpt-6.1-sol", "openai", 100.00, 400, "success"),
]


def seed_ledger(db) -> None:
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    for name in sorted({r[0] for r in _ROWS}):
        if not db.query(Agent).filter_by(name=name).first():
            db.add(Agent(name=name))
    for agent, model, provider, cost, days, status in _ROWS:
        db.add(
            CostEvent(
                agent_name=agent,
                model_name=model,
                provider_name=provider,
                tokens_input=1000,
                tokens_output=100,
                cost=cost,
                cost_source="pricing",
                status=status,
                timestamp=now - timedelta(days=days),
            )
        )
    db.commit()


GOLDEN = [
    {"q": "How much did we spend today?", "total": 13.25, "requests": 3},
    {"q": "What did we spend yesterday?", "total": 6.25, "requests": 2},
    {"q": "How much has the researcher agent spent today?", "total": 10.00},
    {"q": "What was the spend on gpt-6.1-sol yesterday?", "total": 1.25},
    {"q": "How much did anthropic cost today?", "total": 10.75},
    {
        "q": "What is our total spend?",
        "total": 119.75,
        "requests": 7,
        "contains": ["ALL TIME"],
    },
    {
        "q": "How much did we spend in the last fortnight of the moon?",
        "contains": ["NOT understood"],
    },
    {
        "q": "Which agent spent the most today?",
        "total": 13.25,
        "contains": ["researcher"],
    },
    {
        "q": "Compare today vs yesterday spend",
        "total": 13.25,
        "contains": ["[total]"],
    },
    {
        "q": "Show me failures yesterday",
        "contains": ["writer", "failure"],
    },
    {
        "q": "How much did the nosuchagent agent spend today, and did it fail?",
        "contains": ["Window:"],
    },
    {
        "q": "What would today's spend cost on other models?",
        "contains": ["alternatives"],
    },
]
