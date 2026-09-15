"""The default certification task: the production agent path with the release's
model — the same single agent function the seeder and `synth certify` use."""
NAME = "analyst_copilot_release"


def task(item, *, model, lf, llm, prompt_name="analyst-copilot"):
    from synth.agent import answer
    from synth.experiments import question_from_item

    return answer(question_from_item(item), model, live=True, lf=lf, llm=llm,
                  prompt_name=prompt_name).model_dump()
