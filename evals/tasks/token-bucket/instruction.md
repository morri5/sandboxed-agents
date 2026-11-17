The `TokenBucket` in the `throttle` package lets far too many requests through. With `rate=1` and `capacity=3`, after the first three requests empty the bucket, a request every 0.5 seconds keeps being allowed for a long time, although only one request per second should get through.

Fix it: tokens refill at `rate` per second for the time since the last refill (not since the bucket was created), and the bucket never holds more than `capacity` tokens. A refused request takes no tokens. The bucket starts full, and the `clock` argument must still be used for all time measurements.
