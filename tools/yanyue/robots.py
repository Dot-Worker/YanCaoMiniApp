"""Conservative robots rules: longest plain prefix; unsupported syntax pauses."""
import math
from urllib.parse import urlsplit
from .policy import Pause, USER_AGENT

class RobotsRules:
    def __init__(self, text):
        groups = []
        agents, rules, delays = [], [], []
        saw_directive = False
        def flush():
            if agents:
                groups.append((list(agents), list(rules), list(delays)))
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if saw_directive:
                    flush()
                    agents, rules, delays = [], [], []
                    saw_directive = False
                agents.append(value.lower())
            elif key in {"allow", "disallow", "crawl-delay"}:
                saw_directive = True
                if not agents:
                    raise Pause("robots_malformed_group")
                if key == "crawl-delay":
                    try:
                        delay = float(value)
                    except ValueError:
                        raise Pause("robots_invalid_delay") from None
                    if not math.isfinite(delay) or delay < 0:
                        raise Pause("robots_invalid_delay")
                    delays.append(delay)
                else:
                    if any(char in value for char in ("*", "$", "%")):
                        raise Pause("robots_unsupported_pattern_review")
                    if value and not value.startswith("/"):
                        raise Pause("robots_unsupported_path_review")
                    if value:
                        rules.append((value, key == "allow"))
        flush()
        agent = USER_AGENT.lower()
        specific = [(a, r, d) for a, r, d in groups if any(token != "*" and token in agent for token in a)]
        applicable = specific or [(a, r, d) for a, r, d in groups if "*" in a]
        self.rules = [rule for _, rules, _ in applicable for rule in rules]
        self.delay = max([5.0] + [delay for _, _, delays in applicable for delay in delays])

    def can_fetch(self, agent, url):
        path = urlsplit(url).path
        # Query values are not retained or logged, but are part of the match target.
        if urlsplit(url).query:
            path += "?" + urlsplit(url).query
        matches = [(len(prefix), allow) for prefix, allow in self.rules if path.startswith(prefix)]
        return max(matches, default=(0, True))[1]

    def crawl_delay(self, agent):
        return self.delay
