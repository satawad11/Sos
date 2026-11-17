# Battery behaviour in firmware 0.9

While READY and running on battery, <=10% flashes red every five seconds with two short beeps every 15 minutes. <=5% flashes every two seconds with three short beeps every five minutes. Buzzer mute/volume settings remain respected. Emergency indications take priority.

Idle battery sampling occurs every 15 seconds. A change of at least three percentage points, downward crossing of 10%/5%, or a stable mains change triggers a heartbeat, rate-limited to once per minute. Without mains, periodic reports are at most five minutes apart, or two minutes at <=10%; a configured shorter interval remains respected. During an emergency the existing poll traffic carries battery readings instead.

Dashboard battery labels display the age of the most recent normal device uplink. Delivery depends on the radio link and is not continuous streaming. Test battery thresholds and voltage calibration on the physical board before patient deployment; percentage remains an estimate from voltage.
