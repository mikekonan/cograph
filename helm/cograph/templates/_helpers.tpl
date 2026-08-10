{{- define "cograph.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cograph.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "cograph.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "cograph.labels" -}}
app.kubernetes.io/name: {{ include "cograph.name" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cograph.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cograph.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "cograph.backendServiceName" -}}
{{- printf "%s-backend" (include "cograph.fullname" .) -}}
{{- end -}}

{{- define "cograph.webServiceName" -}}
{{- printf "%s-web" (include "cograph.fullname" .) -}}
{{- end -}}

{{- define "cograph.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-env" (include "cograph.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "cograph.checkoutsClaimName" -}}
{{- if .Values.checkouts.existingClaim -}}
{{- .Values.checkouts.existingClaim -}}
{{- else -}}
{{- printf "%s-checkouts" (include "cograph.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "cograph.backendImage" -}}
{{- printf "%s:%s" .Values.images.backend.repository .Values.images.backend.tag -}}
{{- end -}}

{{- define "cograph.webImage" -}}
{{- printf "%s:%s" .Values.images.web.repository .Values.images.web.tag -}}
{{- end -}}

{{- define "cograph.backendUpstream" -}}
{{- if .Values.web.backendUpstream -}}
{{- .Values.web.backendUpstream -}}
{{- else -}}
{{- printf "http://%s:%v" (include "cograph.backendServiceName" .) .Values.backend.service.port -}}
{{- end -}}
{{- end -}}

{{- define "cograph.commonEnv" -}}
- name: COGRAPH_ENVIRONMENT
  value: {{ .Values.app.environment | quote }}
- name: COGRAPH_GIT__CHECKOUTS_ROOT
  value: {{ .Values.app.gitCheckoutsRoot | quote }}
- name: COGRAPH_AUTH__SECURE_COOKIES
  value: {{ ternary "true" "false" .Values.app.auth.secureCookies | quote }}
{{- if .Values.app.embedding.enabled }}
- name: COGRAPH_EMBEDDING__ENABLED
  value: "true"
- name: COGRAPH_EMBEDDING__API_URL
  value: {{ .Values.app.embedding.apiUrl | quote }}
- name: COGRAPH_EMBEDDING__MODEL
  value: {{ .Values.app.embedding.model | quote }}
- name: COGRAPH_EMBEDDING__DIMENSIONS
  value: {{ .Values.app.embedding.dimensions | quote }}
- name: COGRAPH_EMBEDDING__BATCH_SIZE
  value: {{ .Values.app.embedding.batchSize | quote }}
{{- end }}
{{- if .Values.app.completion.enabled }}
- name: COGRAPH_COMPLETION__ENABLED
  value: "true"
- name: COGRAPH_COMPLETION__API_URL
  value: {{ .Values.app.completion.apiUrl | quote }}
- name: COGRAPH_COMPLETION__MODEL
  value: {{ .Values.app.completion.model | quote }}
{{- end }}
{{- end -}}

