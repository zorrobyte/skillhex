from skillhex.outcome import classify_followup, heuristic_followup


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete_json(self, system, user, **kw):
        self.prompts.append(user)
        return self.reply


def test_llm_classification_returns_verdict_and_reason():
    llm = FakeLLM({"verdict": "fail", "reason": "user says wrong city"})
    v = classify_followup(llm, prev_prompt="forecast", prev_answer="Indianapolis 14 days", next_message="No, I'm in Kokomo")
    assert v == ("fail", "user says wrong city")
    assert "Kokomo" in llm.prompts[0] and "Indianapolis" in llm.prompts[0]


def test_llm_unknown_verdict_maps_to_unknown():
    llm = FakeLLM({"verdict": "banana"})
    assert classify_followup(llm, "a", "b", "c")[0] == "unknown"


def test_llm_failure_falls_back_to_heuristic():
    class Boom:
        def complete_json(self, *a, **k):
            raise RuntimeError("down")
    assert classify_followup(Boom(), "a", "b", "that's wrong, try again")[0] == "fail"


def test_heuristic_detects_corrections_and_thanks():
    assert heuristic_followup("No, that's not right") == "fail"
    assert heuristic_followup("thanks, perfect") == "pass"
    assert heuristic_followup("what about tomorrow?") == "unknown"
