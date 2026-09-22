.PHONY: test check build collect verify
test:
	node --experimental-strip-types --test tests/*.test.mjs
	python3 -m unittest discover -s tests -v
check: test
	npm run check
	npm run lint
build:
	npm run build
collect:
	python3 -m ledger collect
verify:
	python3 -m ledger verify
	python3 -m ledger.audit public/data
