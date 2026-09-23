.PHONY: test check build
test:
	node --experimental-strip-types --test tests/*.test.mjs
	python3 -m unittest discover -s tests -v
check: test
	npm run check
	npm run lint
build:
	npm run build
