{{- define "podcast-agent.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "podcast-agent.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "podcast-agent.labels" -}}
app.kubernetes.io/name: {{ include "podcast-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "podcast-agent.selector" -}}
app.kubernetes.io/name: {{ include "podcast-agent.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "podcast-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "podcast-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* The image by digest when one is set (GitOps environments pin digests), otherwise by tag */}}
{{- define "podcast-agent.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/* Shared container settings: env, security, writable scratch volumes for a read-only root fs */}}
{{- define "podcast-agent.containerCommon" -}}
image: {{ include "podcast-agent.image" . | quote }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
securityContext:
  {{- toYaml .Values.securityContext | nindent 2 }}
envFrom:
  - configMapRef:
      name: {{ include "podcast-agent.fullname" . }}
  - secretRef:
      name: {{ .Values.secrets.name }}
      optional: {{ .Values.secrets.optional }}
{{- if .Values.otel.enabled }}
env:
  - name: K8S_POD_NAME
    valueFrom:
      fieldRef: { fieldPath: metadata.name }
  # service.instance.id gives each pod its own metric series. Without it two pods' cumulative counters land on one
  # Prometheus series during a rollout, and the drop from one pod's count to the other's reads as a counter reset.
  - name: OTEL_RESOURCE_ATTRIBUTES
    value: {{ printf "service.instance.id=$(K8S_POD_NAME),k8s.pod.name=$(K8S_POD_NAME),k8s.namespace.name=%s,service.namespace=%s" .Release.Namespace .Release.Name | quote }}
{{- end }}
volumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: cache
    mountPath: /cache
{{- end -}}

{{- define "podcast-agent.volumes" -}}
volumes:
  - name: tmp
    emptyDir: {}
  - name: cache
    emptyDir:
      sizeLimit: 1Gi
{{- end -}}
