# TODO:
- Should we default to internet access allowed? How can we infer whether the
  compose.yaml tried to turn off internet access?
- Consider passing a conversion context or similar so that log messages and exceptions
  can be more informative. (This can be done for exceptions via chaining, but not for
  log messages.)

# Design notes
- I did consider using Pydantic models instead of dicts, but if we want to use Pydantic
  for for validation, we can't build up the model incrementally, so we end up having to
  use a dict anyway. I'm also hesitant to re-write the validation logic which the Helm
  chart already provides (though I recognise that failing sooner may be better).


# Reference
https://docs.docker.com/reference/compose-file/services/
https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/src/k8s_sandbox/resources/helm/agent-env/Chart.yaml
