#!/bin/bash
# Test /frontend-config endpoint performance

URL="http://127.0.0.1:8082/frontend-config"
REQUESTS=20

echo "Testing $URL with $REQUESTS requests..."
echo ""

# Warm up cache with 1 request
curl -s $URL > /dev/null

echo "Response times (ms):"
for i in $(seq 1 $REQUESTS); do
    TIME=$(curl -w "%{time_total}\n" -o /dev/null -s $URL)
    # Convert to milliseconds
    MS=$(echo "$TIME * 1000" | bc)
    printf "Request %2d: %6.2f ms\n" $i $MS
done | tee /tmp/perf_results.txt

echo ""
echo "Statistics:"
awk '{sum+=$3; if(NR==1){min=max=$3}} $3<min{min=$3} $3>max{max=$3} END {print "Average: " sum/NR " ms\nMin: " min " ms\nMax: " max " ms"}' /tmp/perf_results.txt
