青年培训零工与十五分钟就业圈日程规划器
测试：python3 -m unittest discover -s tests -v
构建：python3 -m compileall -q opportunity_planner tests
验收：python3 -m opportunity_planner --database /tmp/opportunity_planner.sqlite3 run --fixture fixtures/acceptance.json --output /tmp/opportunity_planner-output.json
单测：python3 -m unittest tests.test_domain -v
