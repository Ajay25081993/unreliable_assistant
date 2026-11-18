#!/usr/bin/env bash
# 1 controller + 10 injectors: 5,000 threads each = 50,000 VUs. Non-GUI.
jmeter -n -t load_tests/jmeter_50k_plan.jmx -R worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8,worker9,worker10 \
  -l results_50k.jtl -e -o dashboard_50k/
