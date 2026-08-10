# Bugs

## Ring Signal
In this type of alert we shouldn't be looking at the Alipay, Octopus and other wallets datas becauese they don't have hashed pans so most of these alerts that I have here (I loaded the real data) are simply not even true.
Plus for this "impossible geo velocity" alerts we are looking at different transactions done by the same hashed pan so we should rather in transaction list show all transactions from this same hashed pan in the different merchants and highlight the times to show that this is not possible.
In the statistical reasoning we can view the calculations of the distance between the different districts and time delta and speed calculations and that its higher than the treshold set by how much.

If we are looking at transactions that happened from the same hashed pan to different chains of the same store than we should highlight it.
overall there were 65k+ alerts from ring signal indicating something is wrong with the way we are measuing this and I suspect that its due to the fact that maybe the calculation are trying to capture wallets but intsead it should only look at the banks credit cards or debit cards.

Each hashed pan that is having suspicius transactions should be all placed under the same case with one singular alert raised to keep the number of alerts down to sensible number. I understand that a merchant having 5 alerts all 5 alerts disapoear afte the case is judged, I believe that workflow is okay, but I want that the hashed pan and some other cases of family c and b this might simply not be the best way of doing it.

## Empty mcc peer discrepancy alerts
I identified a surge of multiple empty mcc peer discrepancy alerts it looks like all the limited history merchants have this type of alert which doesn't make sense. like i click and then shows 0 alerts fired then why is it there?
Look there is a case of a merchant that had 0 transactions that day but the alert raised was mcc peer discrepancy because their median was 10000 while the mcc median is 324. The alert is raised but when you click in it the shows 0 alerts fire, why is that? present in this location: `/Users/yurdinechimbutane/Documents/GitHub/compliance-pipeline/docs/feedback/examples/example04_1.png` just because they had no transactions that day there should be reason for alerts to fire but says skiped cuz baseline of mrchant not usable, then why was the alert fired in queue in the first place?

## Temporal anomaly
The way that the rank of temporary anomaly is calculated is broken, because all of them are ranked 0.5 and that should change. Maybe use the volume of temporarily abnormal transactions to judge how sever the alert is.

# Suggestion
Place the MCC specific outlier toggle directly under the global one because it is the only one that can be overriden in the tuning tab.

# General
- [ ] **Think about additional improvements based on the feedback given in order to make the dashboard more useful and to reduce the friction of the process.**