// Copyright Jacob Ribbe. Licensed under MIT.
// BPDoctor integration tools — programmatic access to BPDoctor's
// diagnostic scanner, fix system, and result database.
//
// HARD DEPENDENCY: This file requires the BPDoctor plugin (same project).
// BPDoctor headers are included directly. If BPDoctor is not present,
// this file will not compile — remove BPDoctor tools from the module
// registration if building without it.

#include "Tools/BPDoctorScanTool.h"
#include "Tools/BPDoctorResultsTool.h"
#include "Tools/BPDoctorFixTool.h"
#include "Tools/BPDoctorFixAllTool.h"

#include "Animation/AnimBlueprint.h"
#include "Engine/Blueprint.h"
#include "EditorAssetLibrary.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"

// BPDoctor headers — hard dependency, same project
#include "BPDoctorTypes.h"
#include "BPDoctorChecks.h"
#include "BPDoctorScanner.h"
#include "BPDoctorFixes.h"

// Static storage for scan results across tool calls
namespace BPDoctorBridge
{
	static TArray<FBPDoctorResult> LastScanResults;
	static FString LastScanGrade;
	static int32 LastScanErrors = 0;
	static int32 LastScanWarnings = 0;
	static int32 LastScanInfos = 0;

	/** Convert a BPDoctor severity enum to string. */
	static FString SeverityToString(EBPDoctorSeverity Severity)
	{
		switch (Severity)
		{
		case EBPDoctorSeverity::Error:   return TEXT("error");
		case EBPDoctorSeverity::Warning: return TEXT("warning");
		case EBPDoctorSeverity::Info:    return TEXT("info");
		default:                         return TEXT("unknown");
		}
	}

	/** Convert a BPDoctor result to JSON. */
	static TSharedPtr<FJsonObject> ResultToJson(const FBPDoctorResult& Result, int32 Index)
	{
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetNumberField(TEXT("index"), Index);
		Obj->SetStringField(TEXT("severity"), SeverityToString(Result.Severity));
		Obj->SetStringField(TEXT("check_code"), Result.CheckCode);
		Obj->SetStringField(TEXT("asset_path"), Result.AssetPath);
		Obj->SetStringField(TEXT("asset_name"), Result.AssetName);
		Obj->SetStringField(TEXT("description"), Result.Description);
		Obj->SetStringField(TEXT("node_hint"), Result.NodeHint);
		Obj->SetBoolField(TEXT("auto_fixable"), Result.bAutoFixable);
		// HowToFix lives on FBPDoctorCheckDef, not FBPDoctorResult
		const FBPDoctorCheckDef* CheckDef = FBPDoctorChecks::FindCheck(Result.CheckCode);
		Obj->SetStringField(TEXT("how_to_fix"), CheckDef ? CheckDef->HowToFix : TEXT(""));
		return Obj;
	}

	/** Run scanner and flatten AssetInfo results into flat result array. */
	static void RunScanAndFlatten(const FString& AssetPath, const FString& ScanPath)
	{
		LastScanResults.Empty();
		LastScanErrors = 0;
		LastScanWarnings = 0;
		LastScanInfos = 0;

		if (!AssetPath.IsEmpty())
		{
			// Single asset scan via static RunChecks
			UObject* Asset = UEditorAssetLibrary::LoadAsset(AssetPath);
			UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
			if (Blueprint)
			{
				LastScanResults = FBPDoctorChecks::RunChecks(Blueprint);
			}
		}
		else
		{
			// Project scan via scanner instance
			FBPDoctorScanner Scanner;
			if (!ScanPath.IsEmpty() && ScanPath != TEXT("/Game"))
			{
				Scanner.ScanDirectory(ScanPath);
			}
			else
			{
				Scanner.ScanProject();
			}
			// Flatten: AssetInfo.Issues -> flat LastScanResults
			for (const FBPDoctorAssetInfo& Info : Scanner.GetResults())
			{
				LastScanResults.Append(Info.Issues);
			}
		}

		// Count by severity
		for (const FBPDoctorResult& R : LastScanResults)
		{
			switch (R.Severity)
			{
			case EBPDoctorSeverity::Error:   LastScanErrors++; break;
			case EBPDoctorSeverity::Warning: LastScanWarnings++; break;
			case EBPDoctorSeverity::Info:    LastScanInfos++; break;
			}
		}

		// Use BPDoctor's own grade calculation
		LastScanGrade = FBPDoctorScanner::CalculateGrade(LastScanResults);
	}
}

// ==============================================================
// 1. BPDoctorScanTool
// ==============================================================

TSharedPtr<FJsonObject> UBPDoctorScanTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("scan_path"), TEXT("string")},
	}, {});
}

bool UBPDoctorScanTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                 TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString ScanPath = GetStringArg(Args, TEXT("scan_path"), TEXT("/Game"));

	BPDoctorBridge::RunScanAndFlatten(AssetPath, ScanPath);

	if (!AssetPath.IsEmpty() && BPDoctorBridge::LastScanResults.Num() == 0)
	{
		// Check if the asset even existed
		if (!UEditorAssetLibrary::DoesAssetExist(AssetPath))
		{
			OutError = FString::Printf(TEXT("Asset not found: %s"), *AssetPath);
			return false;
		}
	}

	int32 AutoFixable = 0;
	for (const FBPDoctorResult& R : BPDoctorBridge::LastScanResults)
	{
		if (R.bAutoFixable) AutoFixable++;
	}

	// Serialize all results
	TArray<TSharedPtr<FJsonValue>> IssuesJson;
	for (int32 i = 0; i < BPDoctorBridge::LastScanResults.Num(); i++)
	{
		IssuesJson.Add(MakeShared<FJsonValueObject>(
			BPDoctorBridge::ResultToJson(BPDoctorBridge::LastScanResults[i], i)));
	}

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("grade"), BPDoctorBridge::LastScanGrade);
	OutResult->SetNumberField(TEXT("total_issues"), BPDoctorBridge::LastScanResults.Num());
	OutResult->SetNumberField(TEXT("errors"), BPDoctorBridge::LastScanErrors);
	OutResult->SetNumberField(TEXT("warnings"), BPDoctorBridge::LastScanWarnings);
	OutResult->SetNumberField(TEXT("infos"), BPDoctorBridge::LastScanInfos);
	OutResult->SetNumberField(TEXT("auto_fixable"), AutoFixable);
	OutResult->SetArrayField(TEXT("issues"), IssuesJson);
	return true;
}

// ==============================================================
// 2. BPDoctorResultsTool
// ==============================================================

TSharedPtr<FJsonObject> UBPDoctorResultsTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("severity_filter"), TEXT("string")},
		{TEXT("check_code_filter"), TEXT("string")},
		{TEXT("asset_path_filter"), TEXT("string")},
		{TEXT("auto_fixable_only"), TEXT("boolean")},
	}, {});
}

bool UBPDoctorResultsTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                    TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	if (BPDoctorBridge::LastScanResults.Num() == 0)
	{
		OutError = TEXT("No scan results available. Run bpdoctor_scan first.");
		return false;
	}

	const FString SevFilter = GetStringArg(Args, TEXT("severity_filter")).ToLower();
	const FString CodeFilter = GetStringArg(Args, TEXT("check_code_filter"));
	const FString PathFilter = GetStringArg(Args, TEXT("asset_path_filter"));
	const bool bAutoFixOnly = GetBoolArg(Args, TEXT("auto_fixable_only"), false);

	TArray<TSharedPtr<FJsonValue>> FilteredJson;
	for (int32 i = 0; i < BPDoctorBridge::LastScanResults.Num(); i++)
	{
		const FBPDoctorResult& R = BPDoctorBridge::LastScanResults[i];

		if (!SevFilter.IsEmpty() && BPDoctorBridge::SeverityToString(R.Severity) != SevFilter) continue;
		if (!CodeFilter.IsEmpty() && R.CheckCode != CodeFilter) continue;
		if (!PathFilter.IsEmpty() && !R.AssetPath.Contains(PathFilter)) continue;
		if (bAutoFixOnly && !R.bAutoFixable) continue;

		FilteredJson.Add(MakeShared<FJsonValueObject>(BPDoctorBridge::ResultToJson(R, i)));
	}

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetNumberField(TEXT("filtered_count"), FilteredJson.Num());
	OutResult->SetNumberField(TEXT("total_count"), BPDoctorBridge::LastScanResults.Num());
	OutResult->SetStringField(TEXT("grade"), BPDoctorBridge::LastScanGrade);
	OutResult->SetArrayField(TEXT("issues"), FilteredJson);
	return true;
}

// ==============================================================
// 3. BPDoctorFixTool
// ==============================================================

TSharedPtr<FJsonObject> UBPDoctorFixTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("issue_index"), TEXT("integer")},
	}, { TEXT("issue_index") });
}

bool UBPDoctorFixTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const int32 IssueIndex = GetIntArg(Args, TEXT("issue_index"), -1);

	if (IssueIndex < 0 || IssueIndex >= BPDoctorBridge::LastScanResults.Num())
	{
		OutError = FString::Printf(TEXT("Invalid issue_index: %d (valid range: 0-%d)"),
			IssueIndex, BPDoctorBridge::LastScanResults.Num() - 1);
		return false;
	}

	const FBPDoctorResult& Result = BPDoctorBridge::LastScanResults[IssueIndex];
	if (!Result.bAutoFixable)
	{
		const FBPDoctorCheckDef* CheckDef = FBPDoctorChecks::FindCheck(Result.CheckCode);
		FString FixHint = CheckDef ? CheckDef->HowToFix : TEXT("(no fix instructions)");
		OutError = FString::Printf(TEXT("Issue %d (%s) is not auto-fixable. Manual fix required: %s"),
			IssueIndex, *Result.CheckCode, *FixHint);
		return false;
	}

	// Load the Blueprint
	UObject* Asset = UEditorAssetLibrary::LoadAsset(Result.AssetPath);
	UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
	if (!Blueprint)
	{
		OutError = FString::Printf(TEXT("Cannot load Blueprint: %s"), *Result.AssetPath);
		return false;
	}

	// Apply the fix
	bool bFixed = FBPDoctorFixes::ApplyFix(Result, Blueprint);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("fixed"), bFixed);
	OutResult->SetNumberField(TEXT("issue_index"), IssueIndex);
	OutResult->SetStringField(TEXT("check_code"), Result.CheckCode);
	OutResult->SetStringField(TEXT("asset_path"), Result.AssetPath);
	if (!bFixed)
	{
		OutResult->SetStringField(TEXT("error"), TEXT("Fix returned false — check BPDoctor output log for details"));
	}
	return true;
}

// ==============================================================
// 4. BPDoctorFixAllTool
// ==============================================================

TSharedPtr<FJsonObject> UBPDoctorFixAllTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path_filter"), TEXT("string")},
		{TEXT("rescan_after"), TEXT("boolean")},
	}, {});
}

bool UBPDoctorFixAllTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                   TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString PathFilter = GetStringArg(Args, TEXT("asset_path_filter"));
	const bool bRescan = GetBoolArg(Args, TEXT("rescan_after"), true);

	if (BPDoctorBridge::LastScanResults.Num() == 0)
	{
		OutError = TEXT("No scan results available. Run bpdoctor_scan first.");
		return false;
	}

	int32 FixesApplied = 0;
	int32 FixesFailed = 0;
	int32 FixesSkipped = 0;

	// Collect fixable indices by priority (errors first)
	TArray<int32> ErrorIndices, WarningIndices;
	for (int32 i = 0; i < BPDoctorBridge::LastScanResults.Num(); i++)
	{
		const FBPDoctorResult& R = BPDoctorBridge::LastScanResults[i];
		if (!R.bAutoFixable) { FixesSkipped++; continue; }
		if (!PathFilter.IsEmpty() && !R.AssetPath.Contains(PathFilter)) { FixesSkipped++; continue; }

		if (R.Severity == EBPDoctorSeverity::Error) ErrorIndices.Add(i);
		else if (R.Severity == EBPDoctorSeverity::Warning) WarningIndices.Add(i);
	}

	auto ApplyFixByIndex = [&](int32 Index) -> bool
	{
		const FBPDoctorResult& R = BPDoctorBridge::LastScanResults[Index];
		UObject* Asset = UEditorAssetLibrary::LoadAsset(R.AssetPath);
		UBlueprint* BP = Cast<UBlueprint>(Asset);
		if (!BP) return false;
		return FBPDoctorFixes::ApplyFix(R, BP);
	};

	for (int32 Idx : ErrorIndices)
	{
		if (ApplyFixByIndex(Idx)) FixesApplied++;
		else FixesFailed++;
	}
	for (int32 Idx : WarningIndices)
	{
		if (ApplyFixByIndex(Idx)) FixesApplied++;
		else FixesFailed++;
	}

	// Re-scan to verify (always full project for accurate grade)
	int32 RemainingIssues = -1;
	FString NewGrade;
	if (bRescan)
	{
		BPDoctorBridge::RunScanAndFlatten(TEXT(""), TEXT("/Game"));
		RemainingIssues = BPDoctorBridge::LastScanResults.Num();
		NewGrade = BPDoctorBridge::LastScanGrade;
	}

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetNumberField(TEXT("fixes_applied"), FixesApplied);
	OutResult->SetNumberField(TEXT("fixes_failed"), FixesFailed);
	OutResult->SetNumberField(TEXT("fixes_skipped"), FixesSkipped);
	if (RemainingIssues >= 0)
	{
		OutResult->SetNumberField(TEXT("remaining_issues"), RemainingIssues);
		OutResult->SetStringField(TEXT("new_grade"), NewGrade);
	}
	return true;
}
