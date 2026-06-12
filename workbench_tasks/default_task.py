"""The default certification task: the production agent path with the release's
model — the same single agent function the seeder and `synth certify` use."""
NAME = "analyst_copilot_release"


def task(item, *, model, lf, anth, prompt_name="analyst-copilot"):
    from synth.agent import answer

    return answer(item.input, model, live=True, lf=lf, anth=anth,
                  prompt_name=prompt_name).model_dump()
