# B7 Phase 4 / B8 — canary alarms and the automatic-rollback trigger.
#
# AUTHORED DARK: every resource is behind var.b7_canary_enabled (default
# false), so plan/apply create nothing until the activation packet flips
# the flag AND binds the real ALB/target-group dimensions. The thresholds
# are the §A5 triggers verbatim: error rate above 2% for 5 minutes, p95
# above 1.5x the recorded baseline for 10 minutes.
#
# The rollback path is alarm -> EventBridge -> SNS (operator page). The
# EXECUTION of the rollback is scripts/b7_alias_rollback.py per the B8
# runbook — deliberately a human-triggered, dry-run-first script until the
# drill (activation checklist item 6) proves the loop end to end; wiring
# an unattended mutation before a drill would automate an untested path.

variable "b7_canary_enabled" {
  description = "activation flag for the canary alarm set (activation packet only)"
  type        = bool
  default     = false
}

variable "b7_alb_arn_suffix" {
  description = "ALB dimension for the §A5 alarms (bound at activation)"
  type        = string
  default     = ""
}

variable "b7_target_group_arn_suffix" {
  description = "orchestrator target-group dimension (bound at activation)"
  type        = string
  default     = ""
}

variable "b7_p95_baseline_seconds" {
  description = "recorded p95 baseline; alarm fires at 1.5x this (bound at activation)"
  type        = number
  default     = 0
}

resource "aws_sns_topic" "b7_canary" {
  count = var.b7_canary_enabled ? 1 : 0
  name  = "medzen-b7-canary-triggers"
}

resource "aws_cloudwatch_metric_alarm" "error_rate" {
  count               = var.b7_canary_enabled ? 1 : 0
  alarm_name          = "medzen-b7-error-rate-above-2pct-5min"
  alarm_description   = "A5 trigger: 5XX above 2 percent of requests for 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 2
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_pct"
    expression  = "100*(errors/MAX([requests,1]))"
    label       = "5xx percentage"
    return_data = true
  }
  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.b7_alb_arn_suffix
        TargetGroup  = var.b7_target_group_arn_suffix
      }
    }
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.b7_alb_arn_suffix
        TargetGroup  = var.b7_target_group_arn_suffix
      }
    }
  }
  alarm_actions = [aws_sns_topic.b7_canary[0].arn]
}

resource "aws_cloudwatch_metric_alarm" "p95_latency" {
  count               = var.b7_canary_enabled ? 1 : 0
  alarm_name          = "medzen-b7-p95-above-1p5x-baseline-10min"
  alarm_description   = "A5 trigger: p95 above 1.5x the recorded baseline for 10 minutes"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 10
  datapoints_to_alarm = 10
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.b7_p95_baseline_seconds * 1.5
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = var.b7_alb_arn_suffix
    TargetGroup  = var.b7_target_group_arn_suffix
  }
  alarm_actions = [aws_sns_topic.b7_canary[0].arn]
}

resource "aws_cloudwatch_metric_alarm" "readiness" {
  count               = var.b7_canary_enabled ? 1 : 0
  alarm_name          = "medzen-b7-orchestrator-unhealthy-hosts"
  alarm_description   = "A5 trigger: any orchestrator target unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "breaching"
  dimensions = {
    LoadBalancer = var.b7_alb_arn_suffix
    TargetGroup  = var.b7_target_group_arn_suffix
  }
  alarm_actions = [aws_sns_topic.b7_canary[0].arn]
}

resource "aws_cloudwatch_event_rule" "b7_canary_alarm" {
  count       = var.b7_canary_enabled ? 1 : 0
  name        = "medzen-b7-canary-alarm-fired"
  description = "routes any B7 canary alarm state change into the rollback runbook flow"
  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = { value = ["ALARM"] }
      alarmName = [
        "medzen-b7-error-rate-above-2pct-5min",
        "medzen-b7-p95-above-1p5x-baseline-10min",
        "medzen-b7-orchestrator-unhealthy-hosts",
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "b7_canary_page" {
  count = var.b7_canary_enabled ? 1 : 0
  rule  = aws_cloudwatch_event_rule.b7_canary_alarm[0].name
  arn   = aws_sns_topic.b7_canary[0].arn
}
