# Family A almost done

### Review
**Flow diagram**
Review the flow diagram `/Users/yurdinechimbutane/Documents/GitHub/compliance-pipeline/docs/Detection_Flow_Diagrams.md` such that it takes into account the changes we implemented (such as the newly implemented payment method baselines and so on...)

**Transaction highlights**
In the portal some of alerts do not show highlighted the transactions that contributed to it. For example if the alert is raised due to a very high transaction amount (Family A, Test 1), the portal should show the transaction, in the list of ALL transactions, highlighted. like for the case of an alert risen because the card origin is unusual then the highlighting should be done for all transactions that could have contributed to this alert. and the origin of the card should be highlighted as the cause of that transaction being highlighted.

### Family B development
Begin Family B development...
This document `/Users/yurdinechimbutane/Documents/GitHub/compliance-pipeline/docs/superpowers/specs/2026-07-22-detection-layer-design.md` contains the details for Family B ruleset so far.

**Family B ruleset interface**
Just like compliance can manipulate family A tresholds I want them to have an interface to manipulate the different rules and to be able to add some of their own within that tuning tab. Differentiate the tuning sections.

### Family C development
Begin Family C development...
Before that, which exact statistical tests will be used to verify for these items. For example to check if there is some type of structuring going on will you check whether transactions are going to different terminals or branches belonging to the same merchant, but then what will be that treshold? I suggest its a single limit like for the same hashed bin, only use the card on three different branches of the same merchant in a single day but if you use in more than that raise an alert and document it with the relevant data in the platform. The flow diagram `/Users/yurdinechimbutane/Documents/GitHub/compliance-pipeline/docs/Detection_Flow_Diagrams.md` presents other tests that are to be done to attempt to verify these issues and I want all of the stats or rules involved to be correctly documented because right now its just ambiguous and broad and non-explanatory at all. For example impossible geo velocity how will that be checked? there is the option of using the distance between the two points in which the transaction happened divided by the time different between the two and if the average velocity is higher than 1.5m/s (or a reasonable alternate value) then flag the anomaly. But where will we get distance information from? maybe setting up a data base like json or something appropriate for distance between all districts in Hong Kong such that we don't have to pull from google maps whenever the pipeline is running. This document `/Users/yurdinechimbutane/Documents/GitHub/compliance-pipeline/docs/superpowers/specs/2026-07-22-detection-layer-design.md` has most of the answers to the questions, the flow diagram doesn't, so refer to it when development, and make sure their in agreement and that we move forward.

### Pipeline test
Implement the other parts of the pipeline. 
Test and deliver the full pipeline.