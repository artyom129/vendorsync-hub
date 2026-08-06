.PHONY: install test seed run
install:
	python -m pip install -r requirements.txt
test:
	pytest -q
seed:
	python seed_demo.py
run:
	python run.py
