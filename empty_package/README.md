Setup ~/.pypirc

```
[distutils]
  index-servers =
    pypi

[pypi]
  username = __token__
  password = ...
```

The do

```
pip install twine wheel setuptools build
cd inspect-k8s-sandbox
python -m build
twine upload dist/*
```
