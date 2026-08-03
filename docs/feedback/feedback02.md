# Meeting Notes
### Hovering explanations
I want the user to be able to hover on specific tabs and get a small description about what the tab is about.

### Transaction amount baseline per payment method
There is a need for a new baseline analysis to be done per payment method. For example, Alipay must have its own baseline, WeChat pay must have its own baseline etc. This is because the transaction amount patterns for these payment methods are different. An Octopus card making a 3k payment in one transaction is abnormal, but mastercard or visa is acceptible.

### Reviewing sensitivity of the baseline
- Right now there are too many alerts so the stats tools are probably too sensitive.

For example I saw that some alerts for convenience stores were raised because a transaction that was like 150 HKD was made once and its technically an outlier but realistically, that doesn't need to be flagged. If someone is actually money laundering then 150 HKD would be negligeable. 
- A tab for tuning the alert threshold, cut-off, number of days to consider as mature etc

Also, we have the 3.5 treshold for z-score to be considered an outlier, I want a tab that allows the compliance team head to quickly adjust it with one slider. And also adjust mcc specific tresholds because we can allow a little higher volatility for some mcc's.

### Align the wording for specific terms
For example, use the word "transaction" for all of the explanations.
There are too many buzzwords (e.g. case, customer etc) that are refering to the same thing.

### Pagination
When opening the alerts list, the tab takes long to display any alerts because they are all loading at the same time. We should instead show top 20 alerts initially and then at the bottom, allow the user to go to tap the button for next 20 alerts and so on. So judging by the total number of alerts, there will be a different number of pages to display.

### Enhancing the flow of case review
We need to emphasize the indicator that rose the alert.

Right now we go into a specific alert, we get to see all different indicators but we don't know which one exactly fired the alert we clicked into. So when the alert says "High transaction amount than usual", when you land it should show that alert that fired and then there should be another tab which allows the users to see all other indicators related to this specific merchant. Then when we are looking at all the trasactions the highlighted transactions will be the ones specific to the alert, for example if the alert is about high transaction amount than usual, when the user clicks "transactions" tab, the highlighted transactions should be related to the alert but the user must have the chance to click on a filter to highlight all transactions that contribute to firing any indicator. So in the begining it will just show the transactions related to the alert that the user clicked into. And, there should be other filtering options ("all", "high amount alert", "card issuing origin alert", etc) (make use of the accurate wording of the alerts). So when we go into "Why it fired" we show why the alert fired and then we can change the filter to reveal other alerts that also fired whithin the same case, and the same apply for the transactions tab. 

When showing statistical proof, the current three graphs are showing statistical proof for three different things, and not all, and that must be evident. So there should be a dropdown filter in that tab which allows the user to select specific indicators, and then only show graphs for those selected indicators (which could be just one graph, or two, or all three etc). I know that the KDE is related to trasactions outside usual time alert vs own and vs peer baseline, therefore that should be its own category in the filter and if the alert we are viewing is about high amount alert, then KDE must initially be hidden until the default filter (the one related to the alert) is changed to like "all stats" and then we will see KDE for example. I want also as statement explain what each graph is adderssing because the table for median and modified z-score is only related to the three amount alerts, but the box plot is only related to the merchant level vs peer level alert, that must be made obvious with one straight up statement.

### Case follow-through
When an alert is reviewed the compliance team must then make a decision so inside the alert review screen, on top, there should be two buttons the red one says this a follow-through and the other is green and says false-alert. Now when they press the red button they must then fill in a few fields to provide reasoning as to why they thing this is a true alert then the alert is moved to the case follow-through and then it won't show up in the open-alerts tab anymore. If they press the green button they should provide evidence for that aswell, but the alert disappears from the open-alerts tab, and goes to the resolved cases tab in the false alert category. If its a false alert then they should be added back into the training data for the next baseline training which should be in the following 7days. If its a true alert then it stays in the follow through tab and its removed from baseline training for sanitisation of the data.

The case follow-through tab can be developed already.
Providing the compliance ability to update the stage of each case being followed up.
For example, contacted the merchant, and then, documentation received, and then documentation verfied, then case cleared, merchant verified as legitimate and reason statement. 
Or documentation is not provided, then follow-up, and the merchant cannot be contacted so legal action takes place and so on until case is reviewed with a negative outcome then the pipeline already detaily explains what should happen next but basically the case is is resolved in the platform with a negative outcome. 


## Important
- [ ] **Think about additional improvements based on the feedback, and beyond, in order to make the dashboard perfect.**