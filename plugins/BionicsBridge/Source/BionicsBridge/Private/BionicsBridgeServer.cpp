// Copyright Jacob Ribbe. Licensed under MIT.

#include "BionicsBridgeServer.h"
#include "BionicsBridgeModule.h"
#include "BionicsBridgeToolRegistry.h"
#include "BionicsBridgeToolBase.h"

#include "HttpServerModule.h"
#include "HttpServerResponse.h"
#include "HttpServerRequest.h"
#include "IHttpRouter.h"
#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonWriter.h"

FBionicsBridgeServer::FBionicsBridgeServer() {}

FBionicsBridgeServer::~FBionicsBridgeServer()
{
	Stop();
}

/**
 * Echo the request's Origin header IFF it's in the localhost allowlist.
 * Fallback is "http://127.0.0.1" so non-browser clients (curl, Python) still get a valid header.
 * Without this, browsers that reach the bridge via http://localhost get CORS-blocked
 * because their Origin (http://localhost) doesn't equal the fixed http://127.0.0.1 we emit.
 */
static FString ResolveAllowedOrigin(const FHttpServerRequest& Request)
{
	const TArray<FString>* OriginValues = Request.Headers.Find(TEXT("Origin"));
	if (!OriginValues || OriginValues->Num() == 0)
	{
		return TEXT("http://127.0.0.1");
	}
	const FString Origin = (*OriginValues)[0].TrimStartAndEnd();

	// Loopback allowlist: 127.0.0.1 and localhost, both http:// only, optional port.
	static const TArray<FString> AllowedPrefixes = {
		TEXT("http://127.0.0.1"),
		TEXT("http://localhost"),
	};
	for (const FString& Prefix : AllowedPrefixes)
	{
		if (Origin.Equals(Prefix, ESearchCase::IgnoreCase) ||
		    Origin.StartsWith(Prefix + TEXT(":"), ESearchCase::IgnoreCase))
		{
			return Origin;
		}
	}
	return TEXT("http://127.0.0.1");  // Non-loopback — conservative default (won't satisfy browser CORS)
}

bool FBionicsBridgeServer::Start(int32 Port)
{
	if (bRunning) return true;
	if (Port <= 0 || Port > 65535)
	{
		UE_LOG(LogBionicsBridge, Error, TEXT("Invalid port: %d"), Port);
		return false;
	}

	FHttpServerModule& HttpModule = FHttpServerModule::Get();
	TSharedPtr<IHttpRouter> Router = HttpModule.GetHttpRouter(Port);
	if (!Router.IsValid())
	{
		return false;
	}

	// POST /bridge — main JSON-RPC endpoint
	BridgeRoute = Router->BindRoute(
		FHttpPath(TEXT("/bridge")),
		EHttpServerRequestVerbs::VERB_POST,
		FHttpRequestHandler::CreateLambda(
			[this](const FHttpServerRequest& Req, const FHttpResultCallback& OnComplete) -> bool
			{
				return HandleBridgeRequest(Req, OnComplete);
			}));

	// GET /bridge — health check
	HealthRoute = Router->BindRoute(
		FHttpPath(TEXT("/bridge")),
		EHttpServerRequestVerbs::VERB_GET,
		FHttpRequestHandler::CreateLambda(
			[this](const FHttpServerRequest& Req, const FHttpResultCallback& OnComplete) -> bool
			{
				return HandleHealthRequest(Req, OnComplete);
			}));

	// OPTIONS for CORS preflight
	OptionsRoute = Router->BindRoute(
		FHttpPath(TEXT("/bridge")),
		EHttpServerRequestVerbs::VERB_OPTIONS,
		FHttpRequestHandler::CreateLambda(
			[this](const FHttpServerRequest& Req, const FHttpResultCallback& OnComplete) -> bool
			{
				return HandleOptionsRequest(Req, OnComplete);
			}));

	HttpModule.StartAllListeners();
	bRunning = true;
	BoundPort = Port;

	if (AuthToken.IsEmpty())
	{
		UE_LOG(LogBionicsBridge, Warning,
		       TEXT("BionicsBridge auth token is EMPTY — POST /bridge is unauthenticated. "
		            "Set via UBionicsBridgeSubsystem before Start() in production."));
	}
	else
	{
		UE_LOG(LogBionicsBridge, Log,
		       TEXT("BionicsBridge auth enabled (bearer token, %d chars)"), AuthToken.Len());
	}
	return true;
}

bool FBionicsBridgeServer::IsAuthorized(const FHttpServerRequest& Request) const
{
	// Empty token = auth disabled (dev/test only, warned at Start).
	if (AuthToken.IsEmpty()) return true;

	const TArray<FString>* AuthHeaderValues = Request.Headers.Find(TEXT("Authorization"));
	if (!AuthHeaderValues || AuthHeaderValues->Num() == 0)
	{
		return false;
	}

	// Accept "Bearer <token>" (case-insensitive scheme, exact token match).
	const FString& HeaderValue = (*AuthHeaderValues)[0];
	static const FString BearerPrefix = TEXT("Bearer ");
	if (!HeaderValue.StartsWith(BearerPrefix, ESearchCase::IgnoreCase))
	{
		return false;
	}
	FString Provided = HeaderValue.RightChop(BearerPrefix.Len()).TrimStartAndEnd();

	// Constant-time-ish comparison: compare lengths first, then all chars regardless of first mismatch.
	if (Provided.Len() != AuthToken.Len()) return false;
	int32 Diff = 0;
	for (int32 i = 0; i < AuthToken.Len(); ++i)
	{
		Diff |= static_cast<int32>(AuthToken[i]) ^ static_cast<int32>(Provided[i]);
	}
	return Diff == 0;
}

TUniquePtr<FHttpServerResponse> FBionicsBridgeServer::MakeUnauthorizedResponse(const FHttpServerRequest& Request) const
{
	TSharedPtr<FJsonObject> Err = MakeShared<FJsonObject>();
	Err->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
	Err->SetField(TEXT("id"), MakeShared<FJsonValueNull>());
	TSharedPtr<FJsonObject> ErrObj = MakeShared<FJsonObject>();
	ErrObj->SetNumberField(TEXT("code"), -32001);
	ErrObj->SetStringField(TEXT("message"),
		TEXT("Unauthorized: missing or invalid Authorization: Bearer <token>. "
		     "Token is in <ProjectDir>/.bionics-bridge/instance.json."));
	Err->SetObjectField(TEXT("error"), ErrObj);

	FString Out;
	TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
	FJsonSerializer::Serialize(Err.ToSharedRef(), Writer);

	TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(Out, TEXT("application/json"));
	Response->Code = static_cast<EHttpServerResponseCodes>(401);  // Unauthorized (version-portable int cast)
	// Echo the loopback Origin when present so browser-origin clients survive CORS; wildcard would defeat the bearer-auth layer.
	Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { ResolveAllowedOrigin(Request) });
	Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
	Response->Headers.Add(TEXT("WWW-Authenticate"), { TEXT("Bearer") });
	return Response;
}

void FBionicsBridgeServer::Stop()
{
	if (!bRunning) return;
	FHttpServerModule& HttpModule = FHttpServerModule::Get();
	TSharedPtr<IHttpRouter> Router = HttpModule.GetHttpRouter(BoundPort);
	if (Router.IsValid())
	{
		if (BridgeRoute.IsValid()) Router->UnbindRoute(BridgeRoute);
		if (HealthRoute.IsValid()) Router->UnbindRoute(HealthRoute);
		if (OptionsRoute.IsValid()) Router->UnbindRoute(OptionsRoute);
	}
	bRunning = false;
	BoundPort = 0;
}

bool FBionicsBridgeServer::HandleHealthRequest(const FHttpServerRequest& Request,
                                                const FHttpResultCallback& OnComplete)
{
	TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
	Json->SetStringField(TEXT("name"), TEXT("BionicsBridge"));
	Json->SetStringField(TEXT("version"), TEXT("0.1.0"));
	Json->SetBoolField(TEXT("running"), true);
	// Tool count is fingerprinting surface — only expose to authenticated probes.
	// GET /bridge is unauth by design (basic liveness check) so the field is omitted
	// in that case; authenticated callers (agents that already have the bearer) get the count.
	if (IsAuthorized(Request))
	{
		Json->SetNumberField(TEXT("tools"), FBionicsBridgeToolRegistry::Get().Num());
	}

	FString Body;
	TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Body);
	FJsonSerializer::Serialize(Json.ToSharedRef(), Writer);

	TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(Body, TEXT("application/json"));
	// Echo the loopback Origin when present so browser-origin clients survive CORS; wildcard would defeat the bearer-auth layer.
	Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { ResolveAllowedOrigin(Request) });
	Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
	OnComplete(MoveTemp(Response));
	return true;
}

bool FBionicsBridgeServer::HandleOptionsRequest(const FHttpServerRequest& Request,
                                                 const FHttpResultCallback& OnComplete)
{
	TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(TEXT(""), TEXT("text/plain"));
	// Echo the loopback Origin when present so browser-origin clients survive CORS; wildcard would defeat the bearer-auth layer.
	Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { ResolveAllowedOrigin(Request) });
	Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
	Response->Headers.Add(TEXT("Access-Control-Allow-Methods"), { TEXT("POST, GET, OPTIONS") });
	// Include Authorization so browser-side fetch() with Bearer header survives CORS preflight.
	Response->Headers.Add(TEXT("Access-Control-Allow-Headers"), { TEXT("Content-Type, Authorization") });
	OnComplete(MoveTemp(Response));
	return true;
}

bool FBionicsBridgeServer::HandleBridgeRequest(const FHttpServerRequest& Request,
                                                const FHttpResultCallback& OnComplete)
{
	// Auth gate — reject unauthenticated POSTs before any parsing work.
	if (!IsAuthorized(Request))
	{
		UE_LOG(LogBionicsBridge, Warning, TEXT("Rejected unauthenticated POST /bridge"));
		OnComplete(MakeUnauthorizedResponse(Request));
		return true;
	}

	// Precompute the CORS origin once — used on every response path below.
	const FString AllowedOrigin = ResolveAllowedOrigin(Request);

	// Reject oversized payloads (1 MB max — protects against unbounded allocation)
	static constexpr int32 MaxBodyBytes = 1 * 1024 * 1024;
	if (Request.Body.Num() > MaxBodyBytes)
	{
		UE_LOG(LogBionicsBridge, Warning, TEXT("Request body too large: %d bytes (max %d)"), Request.Body.Num(), MaxBodyBytes);
		TSharedPtr<FJsonObject> Err = MakeErrorResponse(nullptr, -32700,
			FString::Printf(TEXT("Request too large: %d bytes (max %d)"), Request.Body.Num(), MaxBodyBytes));
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Err.ToSharedRef(), Writer);
		TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(Out, TEXT("application/json"));
		// Echo loopback Origin (precomputed above) so browser clients survive CORS.
		Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { AllowedOrigin });
		Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
		OnComplete(MoveTemp(Response));
		return true;
	}

	// Parse request body — UTF-8 bytes → FString
	FString BodyStr;
	if (Request.Body.Num() > 0)
	{
		FUTF8ToTCHAR Conv(reinterpret_cast<const ANSICHAR*>(Request.Body.GetData()), Request.Body.Num());
		BodyStr = FString(Conv.Length(), Conv.Get());
	}

	TSharedPtr<FJsonObject> RequestJson;
	TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(BodyStr);
	if (!FJsonSerializer::Deserialize(Reader, RequestJson) || !RequestJson.IsValid())
	{
		TSharedPtr<FJsonObject> Err = MakeErrorResponse(nullptr, -32700, TEXT("Parse error"));
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Err.ToSharedRef(), Writer);
		TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(Out, TEXT("application/json"));
		// Echo loopback Origin (precomputed above) so browser clients survive CORS.
		Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { AllowedOrigin });
		Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
		OnComplete(MoveTemp(Response));
		return true;
	}

	// Marshal dispatch to game thread (all UE API calls must be on game thread)
	AsyncTask(ENamedThreads::GameThread,
		[this, RequestJson, OnComplete, AllowedOrigin]()
		{
			TSharedPtr<FJsonObject> ResponseJson;
			DispatchRpc(RequestJson, ResponseJson);

			FString Out;
			if (ResponseJson.IsValid())
			{
				TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
				FJsonSerializer::Serialize(ResponseJson.ToSharedRef(), Writer);
			}

			TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(Out, TEXT("application/json"));
			// Echo loopback Origin (captured from the pre-dispatch request) so browser clients survive CORS.
			Response->Headers.Add(TEXT("Access-Control-Allow-Origin"), { AllowedOrigin });
			Response->Headers.Add(TEXT("Vary"), { TEXT("Origin") });
			OnComplete(MoveTemp(Response));
		});

	return true;
}

void FBionicsBridgeServer::DispatchRpc(const TSharedPtr<FJsonObject>& Req,
                                        TSharedPtr<FJsonObject>& OutResponse)
{
	TSharedPtr<FJsonValue> IdValue = Req->TryGetField(TEXT("id"));
	FString Method;
	Req->TryGetStringField(TEXT("method"), Method);

	if (Method == TEXT("initialize"))
	{
		OutResponse = MakeShared<FJsonObject>();
		OutResponse->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
		if (IdValue.IsValid()) OutResponse->SetField(TEXT("id"), IdValue);
		TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("protocolVersion"), TEXT("2024-11-05"));
		TSharedPtr<FJsonObject> Capabilities = MakeShared<FJsonObject>();
		Capabilities->SetObjectField(TEXT("tools"), MakeShared<FJsonObject>());
		Result->SetObjectField(TEXT("capabilities"), Capabilities);
		TSharedPtr<FJsonObject> ServerInfo = MakeShared<FJsonObject>();
		ServerInfo->SetStringField(TEXT("name"), TEXT("BionicsBridge"));
		ServerInfo->SetStringField(TEXT("version"), TEXT("0.1.0"));
		Result->SetObjectField(TEXT("serverInfo"), ServerInfo);
		OutResponse->SetObjectField(TEXT("result"), Result);
	}
	else if (Method == TEXT("tools/list"))
	{
		OutResponse = MakeShared<FJsonObject>();
		OutResponse->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
		if (IdValue.IsValid()) OutResponse->SetField(TEXT("id"), IdValue);
		OutResponse->SetObjectField(TEXT("result"),
			FBionicsBridgeToolRegistry::Get().GetToolsListJson());
	}
	else if (Method == TEXT("tools/call"))
	{
		const TSharedPtr<FJsonObject>* ParamsPtr = nullptr;
		if (!Req->TryGetObjectField(TEXT("params"), ParamsPtr) || !ParamsPtr)
		{
			OutResponse = MakeErrorResponse(IdValue, -32602, TEXT("Invalid params"));
			return;
		}
		FString ToolName;
		(*ParamsPtr)->TryGetStringField(TEXT("name"), ToolName);
		const TSharedPtr<FJsonObject>* ArgsPtr = nullptr;
		(*ParamsPtr)->TryGetObjectField(TEXT("arguments"), ArgsPtr);
		TSharedPtr<FJsonObject> Args = ArgsPtr ? *ArgsPtr : MakeShared<FJsonObject>();

		UBionicsBridgeToolBase* Tool = FBionicsBridgeToolRegistry::Get().FindTool(ToolName);
		if (!Tool)
		{
			OutResponse = MakeErrorResponse(IdValue, -32601, FString::Printf(TEXT("Unknown tool: %s"), *ToolName));
			return;
		}

		TSharedPtr<FJsonObject> ToolResult;
		FString ToolError;
		// Note: UE disables C++ exceptions by default (bEnableExceptions=false).
		// Tool failures must be reported via return value + OutError, not exceptions.
		bool bOk = Tool->Execute(Args, ToolResult, ToolError);

		OutResponse = MakeShared<FJsonObject>();
		OutResponse->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
		if (IdValue.IsValid()) OutResponse->SetField(TEXT("id"), IdValue);

		TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
		TArray<TSharedPtr<FJsonValue>> ContentArr;
		TSharedPtr<FJsonObject> TextContent = MakeShared<FJsonObject>();
		TextContent->SetStringField(TEXT("type"), TEXT("text"));
		if (bOk && ToolResult.IsValid())
		{
			FString ResultStr;
			TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&ResultStr);
			FJsonSerializer::Serialize(ToolResult.ToSharedRef(), Writer);
			TextContent->SetStringField(TEXT("text"), ResultStr);
		}
		else
		{
			TextContent->SetStringField(TEXT("text"), ToolError);
		}
		ContentArr.Add(MakeShared<FJsonValueObject>(TextContent));
		Result->SetArrayField(TEXT("content"), ContentArr);
		Result->SetBoolField(TEXT("isError"), !bOk);
		OutResponse->SetObjectField(TEXT("result"), Result);
	}
	else if (Method == TEXT("notifications/initialized"))
	{
		// JSON-RPC 2.0: notifications (no id) MUST NOT receive a response.
		// OutResponse stays invalid → HandleBridgeRequest skips the response.
	}
	else if (Method == TEXT("shutdown"))
	{
		OutResponse = MakeShared<FJsonObject>();
		OutResponse->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
		if (IdValue.IsValid()) OutResponse->SetField(TEXT("id"), IdValue);
		OutResponse->SetObjectField(TEXT("result"), MakeShared<FJsonObject>());
	}
	else
	{
		OutResponse = MakeErrorResponse(IdValue, -32601,
			FString::Printf(TEXT("Method not found: %s"), *Method));
	}
}

TSharedPtr<FJsonObject> FBionicsBridgeServer::MakeErrorResponse(
	const TSharedPtr<FJsonValue>& Id, int32 Code, const FString& Message)
{
	TSharedPtr<FJsonObject> Res = MakeShared<FJsonObject>();
	Res->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
	if (Id.IsValid()) Res->SetField(TEXT("id"), Id);
	TSharedPtr<FJsonObject> Err = MakeShared<FJsonObject>();
	Err->SetNumberField(TEXT("code"), Code);
	Err->SetStringField(TEXT("message"), Message);
	Res->SetObjectField(TEXT("error"), Err);
	return Res;
}
