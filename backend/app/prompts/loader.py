import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent
_VERSION_FILE = re.compile(r"^v(\d+)\.md$")


class PromptNotFoundError(LookupError):
    pass


class PromptRenderError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt template stored at app/prompts/<name>/v<N>.md.

    Placeholders use string.Template syntax ($name or ${name}), so JSON braces need no escaping.
    The sha256 identifies the exact text that was sent, for the audit log.
    """

    name: str
    version: int
    text: str
    sha256: str

    def render(self, **variables: object) -> str:
        try:
            return Template(self.text).substitute(variables)
        except KeyError as exc:
            raise PromptRenderError(
                f"Prompt {self.name} v{self.version} is missing variable {exc.args[0]!r}"
            ) from None
        except ValueError as exc:
            raise PromptRenderError(f"Prompt {self.name} v{self.version}: {exc}") from None


def available_versions(name: str, root: Path = PROMPTS_DIR) -> list[int]:
    directory = root / name
    if not directory.is_dir():
        return []
    versions = (_VERSION_FILE.match(path.name) for path in directory.iterdir())
    return sorted(int(match.group(1)) for match in versions if match)


@lru_cache
def load_prompt(name: str, version: int | None = None, root: Path = PROMPTS_DIR) -> Prompt:
    """Load a prompt by name. With no version, the highest vN.md wins."""
    versions = available_versions(name, root)
    if not versions:
        raise PromptNotFoundError(f"No prompt named {name!r}")
    chosen = versions[-1] if version is None else version
    if chosen not in versions:
        raise PromptNotFoundError(f"Prompt {name!r} has no version {chosen}")
    text = (root / name / f"v{chosen}.md").read_text(encoding="utf-8")
    return Prompt(
        name=name,
        version=chosen,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
