"""AI planning agent for PawPal+.

The agent turns a plain-English goal ("I just adopted a puppy, set up a week of
care") into real tasks on the owner's pets. It works in a plan -> act -> check
loop and the checks are PawPal's own scheduler logic, not the model's opinion:

    1. PLAN   The model proposes tasks as JSON.
    2. VALIDATE  Guardrails reject malformed or unsafe items (unknown pet, bad
                 duration, bad priority, ...). Rejections are fed back to the model.
    3. ACT    Valid tasks are added to a *copy* of the owner (a dry run).
    4. CHECK  Scheduler.detect_conflicts() and Scheduler.generate_plan() report
              new time conflicts and tasks that don't fit the owner's minutes.
    5. REVISE If problems were found, they go back to the model for another try
              (up to `max_revisions` times). Otherwise the tasks are committed.

Nothing touches the real owner until the final step, so a failed run leaves the
user's data unchanged. The model is reached through the small `LLMClient`
interface so tests and the eval harness can swap in a scripted fake.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from pawpal_system import Frequency, Owner, Priority, Scheduler, Task, format_time

logger = logging.getLogger("pawpal.agent")
logging.getLogger("pawpal").addHandler(logging.NullHandler())  # quiet unless setup_logging() is called

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
MAX_TASKS_PER_RUN = 30
MAX_DURATION_MINUTES = 240

SYSTEM_PROMPT = """You are the planning engine inside PawPal+, a pet-care scheduler.
Turn the owner's goal into concrete care tasks for the pets that already exist.

Reply with ONLY a JSON object, no prose, in exactly this shape:
{"tasks": [{"pet": "<existing pet name>", "title": "<short title>",
            "duration_minutes": <integer 1-240>,
            "priority": "low" | "medium" | "high",
            "frequency": "once" | "daily" | "weekly",
            "preferred_time": "HH:MM" (24-hour) or null}]}

Rules:
- Only use pet names from the list you are given.
- The owner has a limited number of minutes per day: keep the daily total realistic.
- Do not give two tasks overlapping preferred times (a task occupies
  preferred_time to preferred_time + duration_minutes).
- Do not repeat a task the pet already has (same title and same time). Two feedings at
  different times are fine and are not duplicates.
- If the owner wants minutes between tasks, leave at least that gap between one task
  ending and the next starting.
- Respect the owner's day: never set a preferred_time during a busy block or outside
  the day start/end you are given.
- If you are told about problems with your previous attempt, fix them and
  return the complete corrected task list."""


def load_env(path: str = ".env") -> None:
    """Load KEY=value lines from a .env file into os.environ (real env vars win).

    A tiny stand-in for python-dotenv so there is nothing extra to install.
    Missing file is fine. Lines starting with # and blank lines are ignored.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


load_env()


class AgentError(Exception):
    """Raised when the agent cannot produce anything usable (e.g. the API is down)."""


class LLMClient(Protocol):
    """Anything that can turn a system prompt and chat messages into text."""

    def complete(self, system: str, messages: list[dict]) -> str: ...


class AnthropicClient:
    """LLMClient backed by the Anthropic API. Needs ANTHROPIC_API_KEY in the environment."""

    def __init__(self, model: str | None = None, max_tokens: int = 2000) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AgentError("ANTHROPIC_API_KEY is not set. See the README for setup steps.")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise AgentError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(timeout=60.0, max_retries=2)
        self.model = model or os.environ.get("PAWPAL_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict]) -> str:
        try:
            response = self._client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system, messages=messages
            )
        except self._anthropic.APIError as exc:
            logger.error("Anthropic API call failed: %s", exc)
            raise AgentError(f"The AI service could not be reached: {exc}") from exc
        return "".join(block.text for block in response.content if block.type == "text")


class GeminiClient:
    """LLMClient backed by Google's Gemini API (has a free tier).

    Needs GEMINI_API_KEY (or GOOGLE_API_KEY). Uses the REST API through the standard
    library, so no extra package is required.
    """

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    RETRY_DELAYS = (3, 8)  # seconds to wait before each retry of a busy or rate-limited call
    RETRYABLE_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, model: str | None = None, timeout: float = 60.0) -> None:
        self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise AgentError("GEMINI_API_KEY is not set. See the README for setup steps.")
        self.model = model or os.environ.get("PAWPAL_MODEL", DEFAULT_GEMINI_MODEL)
        self.timeout = timeout
        self._sleep = time.sleep  # replaceable in tests

    def complete(self, system: str, messages: list[dict]) -> str:
        import urllib.error
        import urllib.request

        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        }
        request = urllib.request.Request(
            self.URL.format(model=self.model),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        data = None
        for attempt, delay in enumerate(self.RETRY_DELAYS + (None,)):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read())["error"]["message"][:200]
                except Exception:
                    detail = ""
                logger.error("Gemini API returned HTTP %s: %s", exc.code, detail)
                if exc.code in self.RETRYABLE_STATUS and delay is not None:
                    logger.info("Retrying in %ss (attempt %d)", delay, attempt + 1)
                    self._sleep(delay)
                    continue
                hint = " Rate limit: wait a minute and retry." if exc.code == 429 else ""
                raise AgentError(f"The AI service returned an error (HTTP {exc.code}): {detail}{hint}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.error("Gemini API call failed: %s", exc)
                if delay is not None:
                    self._sleep(delay)
                    continue
                raise AgentError(f"The AI service could not be reached: {exc}") from exc

        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected Gemini response shape: %.200s", data)
            raise AgentError("The AI service returned an empty or blocked response.") from exc


def make_client() -> LLMClient:
    """Pick a provider: PAWPAL_PROVIDER=gemini|anthropic, else whichever API key is set."""
    provider = os.environ.get("PAWPAL_PROVIDER", "").lower()
    if not provider:
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            provider = "gemini"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            raise AgentError("No AI key found. Set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY. See the README.")
    if provider == "gemini":
        return GeminiClient()
    if provider == "anthropic":
        return AnthropicClient()
    raise AgentError(f"Unknown PAWPAL_PROVIDER {provider!r}; use 'gemini' or 'anthropic'.")


@dataclass
class TaskSpec:
    """A validated task proposal, not yet attached to a pet."""

    pet: str
    title: str
    duration_minutes: int
    priority: Priority
    frequency: Frequency
    preferred_time: int | None

    def to_task(self) -> Task:
        return Task(
            self.title,
            self.duration_minutes,
            self.priority,
            self.frequency,
            preferred_time=self.preferred_time,
        )


@dataclass
class AgentStep:
    """One line of the agent's visible trace. kind: plan, validate, check, revise, commit."""

    kind: str
    detail: str


@dataclass
class AgentResult:
    """What the agent did. `success` means the final plan passed every check."""

    goal: str
    tasks: list[TaskSpec] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # problems still unresolved at the end
    duplicates: list[str] = field(default_factory=list)  # proposed tasks skipped as already present
    attempts: int = 0
    committed: bool = False

    @property
    def success(self) -> bool:
        return self.committed and not self.issues


def parse_time(value) -> int | None:
    """'08:30' -> 510. None/empty -> None. Raises ValueError for anything else."""
    if value in (None, "", "null"):
        return None
    if not isinstance(value, str):
        raise ValueError(f"preferred_time must be 'HH:MM' text, got {value!r}")
    hours, sep, minutes = value.strip().partition(":")
    if not sep or not hours.isdigit() or not minutes.isdigit():
        raise ValueError(f"preferred_time {value!r} is not in HH:MM format")
    hour, minute = int(hours), int(minutes)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"preferred_time {value!r} is not a real time of day")
    return hour * 60 + minute


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating code fences or extra prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contained no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError('JSON must be an object with a "tasks" list')
    return data


def validate_tasks(raw_tasks: list, pet_names: list[str]) -> tuple[list[TaskSpec], list[str]]:
    """Guardrail: keep well-formed tasks and describe every rejected one.

    Returns (valid specs, error messages). Never raises on bad model output.
    """
    by_lower = {name.lower(): name for name in pet_names}
    specs: list[TaskSpec] = []
    errors: list[str] = []

    if len(raw_tasks) > MAX_TASKS_PER_RUN:
        errors.append(f"too many tasks ({len(raw_tasks)}); the limit is {MAX_TASKS_PER_RUN}")
        raw_tasks = raw_tasks[:MAX_TASKS_PER_RUN]

    for index, raw in enumerate(raw_tasks, start=1):
        label = f"task {index}"
        try:
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            pet = by_lower.get(str(raw.get("pet", "")).strip().lower())
            if pet is None:
                raise ValueError(f"unknown pet {raw.get('pet')!r} (pets are: {', '.join(pet_names)})")
            title = str(raw.get("title", "")).strip()
            if not title:
                raise ValueError("missing title")
            duration = raw.get("duration_minutes")
            if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= MAX_DURATION_MINUTES:
                raise ValueError(f"duration_minutes must be an integer 1-{MAX_DURATION_MINUTES}, got {duration!r}")
            try:
                priority = Priority[str(raw.get("priority", "")).strip().upper()]
            except KeyError:
                raise ValueError(f"priority must be low, medium or high, got {raw.get('priority')!r}") from None
            try:
                frequency = Frequency(str(raw.get("frequency", "once")).strip().lower())
            except ValueError:
                raise ValueError(f"frequency must be once, daily or weekly, got {raw.get('frequency')!r}") from None
            specs.append(TaskSpec(pet, title, duration, priority, frequency, parse_time(raw.get("preferred_time"))))
        except ValueError as exc:
            errors.append(f"{label} ({raw.get('title') if isinstance(raw, dict) else raw!r}): {exc}")

    return specs, errors


def describe_context(owner: Owner) -> str:
    """The facts about the owner that are sent to the model along with the goal.

    The UI shows this same text before the user types a goal, so what they see is
    exactly what the model is told.
    """

    def describe_task(task: Task) -> str:
        at = f" at {format_time(task.preferred_time)}" if task.preferred_time is not None else ""
        return f"    - {task.title}: {task.duration_minutes} min, {task.priority.name.lower()}, {task.frequency.value}{at}"

    pet_lines = []
    for pet in owner.pets:
        pending = [task for task in pet.tasks if not task.completed]
        pet_lines.append(f"- {pet.name} ({pet.species}), existing tasks: {'none' if not pending else ''}".rstrip())
        pet_lines.extend(describe_task(task) for task in pending)

    day = f"the day starts at {format_time(owner.day_start_minute)}"
    if owner.day_end_minute is not None:
        day += f" and ends at {format_time(owner.day_end_minute)}"
    busy = ""
    if owner.busy_blocks:
        blocks = ", ".join(f"{format_time(a)}-{format_time(b)}" for a, b in owner.busy_blocks)
        busy = f" The owner is busy (no tasks possible) during: {blocks}."
    buffer = owner.preferences.buffer_minutes
    gap = f" Leave at least {buffer} minute(s) between tasks." if buffer else ""
    return (
        "Pets:\n" + "\n".join(pet_lines) + "\n\n"
        f"Owner has {owner.available_minutes} minutes available per day and {day}.{gap}{busy}"
    )


class PlannerAgent:
    """Plans pet-care tasks from a goal, checking its own work with the Scheduler."""

    def __init__(self, llm: LLMClient, max_revisions: int = 3) -> None:
        self.llm = llm
        self.max_revisions = max_revisions

    # ---- public entry point -------------------------------------------------

    def run(self, owner: Owner, goal: str) -> AgentResult:
        """Plan tasks for `goal`, commit them to `owner` if they pass, and return a trace.

        Raises AgentError if the goal is empty, there are no pets, or the model never
        returned a usable answer. In every error case the owner is left unchanged.
        """
        goal = goal.strip()
        if not goal:
            raise AgentError("Please describe what you want planned.")
        if not owner.pets:
            raise AgentError("Add at least one pet before using the AI planner.")

        result = AgentResult(goal=goal)
        pet_names = [pet.name for pet in owner.pets]
        messages = [{"role": "user", "content": self._describe_request(owner, goal)}]
        logger.info("Agent started: goal=%r pets=%s", goal, pet_names)

        best: list[TaskSpec] | None = None
        best_issues: list[str] = []
        best_dups: list[str] = []

        for attempt in range(1, self.max_revisions + 2):
            result.attempts = attempt
            reply = self._ask(messages)
            messages.append({"role": "assistant", "content": reply})

            # PLAN + VALIDATE
            try:
                raw_tasks = extract_json(reply)["tasks"]
            except ValueError as exc:
                problems = [f"Your reply could not be parsed: {exc}"]
                result.steps.append(AgentStep("validate", problems[0]))
                logger.warning("Attempt %d unparseable: %s", attempt, exc)
                messages.append({"role": "user", "content": self._feedback(problems)})
                continue

            specs, errors = validate_tasks(raw_tasks, pet_names)
            result.steps.append(AgentStep("plan", f"Attempt {attempt}: model proposed {len(raw_tasks)} task(s)"))
            for error in errors:
                result.steps.append(AgentStep("validate", f"Rejected {error}"))
                logger.warning("Attempt %d rejected %s", attempt, error)

            specs, dups = self._drop_duplicates(owner, specs)
            for dup in dups:
                result.steps.append(AgentStep("validate", f"Skipped duplicate: {dup}"))

            # ACT (dry run) + CHECK
            problems = list(errors)
            if specs:
                problems += self._check(owner, specs)
            elif not dups:
                problems.append("No valid tasks were produced.")
            for problem in problems[len(errors):]:
                result.steps.append(AgentStep("check", problem))

            # A reply made only of duplicates is still usable: there is just nothing new to add.
            if (specs or dups) and (best is None or len(problems) <= len(best_issues)):
                best, best_issues, best_dups = specs, problems, dups

            if not problems:
                result.steps.append(AgentStep("check", "All checks passed: no new conflicts, everything fits."))
                break

            if attempt <= self.max_revisions:
                result.steps.append(AgentStep("revise", "Sending the problems back to the model to fix."))
                messages.append({"role": "user", "content": self._feedback(problems)})

        if best is None:
            raise AgentError("The AI did not return a usable plan. Nothing was changed.")

        # COMMIT the best attempt (the passing one, or the least-bad if none passed).
        result.tasks = best
        result.issues = best_issues
        result.duplicates = best_dups
        self._commit(owner, best)
        result.committed = True
        outcome = "all checks passed" if not best_issues else f"{len(best_issues)} issue(s) remain"
        skipped = f", skipped {len(best_dups)} duplicate(s)" if best_dups else ""
        result.steps.append(AgentStep("commit", f"Added {len(best)} task(s) to your pets{skipped}; {outcome}."))
        logger.info("Agent finished: %d tasks, attempts=%d, issues=%d", len(best), result.attempts, len(best_issues))
        return result

    # ---- helpers ------------------------------------------------------------

    def _ask(self, messages: list[dict]) -> str:
        try:
            return self.llm.complete(SYSTEM_PROMPT, messages)
        except AgentError:
            raise
        except Exception as exc:  # any client failure must become a safe, friendly error
            logger.exception("LLM client failed")
            raise AgentError(f"The AI service failed: {exc}") from exc

    @staticmethod
    def _describe_request(owner: Owner, goal: str) -> str:
        return f"Goal: {goal}\n\n{describe_context(owner)}"

    @staticmethod
    def _drop_duplicates(owner: Owner, specs: list[TaskSpec]) -> tuple[list[TaskSpec], list[str]]:
        """Split specs into new ones and duplicates.

        A duplicate is the same pet, same title (ignoring case) and same preferred time as a
        pending task, or as an earlier proposal in this same batch. So a second feeding at a
        different time is NOT a duplicate.
        """

        def key(pet: str, title: str, time_: int | None) -> tuple:
            return (pet.lower(), title.strip().lower(), time_)

        seen = {
            key(pet.name, task.title, task.preferred_time)
            for pet in owner.pets
            for task in pet.tasks
            if not task.completed
        }
        fresh, dups = [], []
        for spec in specs:
            k = key(spec.pet, spec.title, spec.preferred_time)
            if k in seen:
                when = f" at {format_time(spec.preferred_time)}" if spec.preferred_time is not None else ""
                dups.append(f"{spec.title}{when} ({spec.pet})")
            else:
                seen.add(k)
                fresh.append(spec)
        return fresh, dups

    @staticmethod
    def _feedback(problems: list[str]) -> str:
        return (
            "Your previous attempt has these problems:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\nReturn the complete corrected JSON task list."
        )

    @staticmethod
    def _check(owner: Owner, specs: list[TaskSpec]) -> list[str]:
        """Dry-run the specs on a copy of the owner and report problems the new tasks cause."""
        trial = copy.deepcopy(owner)
        baseline = set(Scheduler(trial).detect_conflicts())

        new_tasks = []
        for spec in specs:
            task = spec.to_task()
            next(pet for pet in trial.pets if pet.name == spec.pet).add_task(task)
            new_tasks.append(task)

        scheduler = Scheduler(trial)
        problems = [c for c in scheduler.detect_conflicts() if c not in baseline]
        new_ids = {task.id for task in new_tasks}

        # Tasks that don't overlap but sit closer together than the owner's buffer.
        buffer = owner.preferences.buffer_minutes
        if buffer > 0:
            timed = sorted(
                (t for t in trial.get_all_tasks() if t.preferred_time is not None and scheduler._is_pending(t, date.today())),
                key=lambda t: t.preferred_time,
            )
            for first, second in zip(timed, timed[1:]):
                gap = second.preferred_time - first.preferred_end
                if 0 <= gap < buffer and (first.id in new_ids or second.id in new_ids):
                    problems.append(
                        f"'{first.title}' ends at {format_time(first.preferred_end)} and '{second.title}' starts at "
                        f"{format_time(second.preferred_time)}: leave at least {buffer} minutes between tasks"
                    )

        for spec in specs:
            if spec.preferred_time is None:
                continue
            start, end = spec.preferred_time, spec.preferred_time + spec.duration_minutes
            label = f"'{spec.title}' ({spec.pet}) at {format_time(start)}"
            for block_start, block_end in owner.busy_blocks:
                if start < block_end and block_start < end:
                    problems.append(
                        f"{label} falls during busy time {format_time(block_start)}-{format_time(block_end)}"
                    )
            if start < owner.day_start_minute:
                problems.append(f"{label} is before the day starts at {format_time(owner.day_start_minute)}")
            if owner.day_end_minute is not None and end > owner.day_end_minute:
                problems.append(f"{label} runs past the day end at {format_time(owner.day_end_minute)}")

        plan = scheduler.generate_plan()
        for skipped in plan.skipped_items:
            if skipped.task.id in new_ids:
                problems.append(
                    f"'{skipped.task.title}' ({skipped.task.pet_name}) does not fit: {skipped.reason}"
                )
        return problems

    @staticmethod
    def _commit(owner: Owner, specs: list[TaskSpec]) -> None:
        pets = {pet.name: pet for pet in owner.pets}
        for spec in specs:
            pets[spec.pet].add_task(spec.to_task())


def setup_logging(path: str = "pawpal.log") -> None:
    """Send PawPal's logs to a file (once). Safe to call on every Streamlit re-run."""
    root = logging.getLogger("pawpal")
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
