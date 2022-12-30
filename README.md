# Installing Dockerw

## From Source

From root of repo
```bash
python3 -m pip install .
```

## From GitHub

To install latest version from GitHub
```bash
python3 -m pip install git+https://github.com/kschwab/dockerw@main
```

To install specific version from GitHub
```bash
python3 -m pip install git+https://github.com/kschwab/dockerw@<VERSION>
```

## Script Only

To install latest version of script
```bash
wget -nv https://raw.githubusercontent.com/kschwab/dockerw/main/dockerw/dockerw.py -O dockerw && chmod a+x dockerw
```

To install specific version of script
```bash
wget -nv https://raw.githubusercontent.com/kschwab/dockerw/<VERSION>/dockerw/dockerw.py -O dockerw && chmod a+x dockerw
```

# Uninstalling Dockerw

```bash
python3 -m pip uninstall dockerw -y
```
