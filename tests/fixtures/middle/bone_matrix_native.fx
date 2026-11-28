// Independent controller diagnostic; applied to an actual PMX-derived mesh.
#ifndef BONE_CHANNEL
#define BONE_CHANNEL 0
#endif
float4x4 WVP : WORLDVIEWPROJECTION;
float4x4 Bone : CONTROLOBJECT < string name="(self)"; string item="ProbeArm"; >;
float3 Center : CONTROLOBJECT < string name="(self)"; string item="ProbeCenter"; >;
float4 VS(float4 p:POSITION):POSITION { return mul(p,WVP); }
float4 PS():COLOR {
#if BONE_CHANNEL == 0
    return float4(Bone[0].xyz*0.25+0.5,1);
#elif BONE_CHANNEL == 1
    return float4(Bone[1].xyz*0.25+0.5,1);
#elif BONE_CHANNEL == 2
    return float4(Bone[2].xyz*0.25+0.5,1);
#elif BONE_CHANNEL == 3
    return float4(Bone[3].xyz/40+0.5,1);
#else
    return float4(Center/40+0.5,1);
#endif
}
technique Model < string MMDPass="object"; > {
    pass Main {
        VertexShader=compile vs_3_0 VS();
        PixelShader=compile ps_3_0 PS();
    }
}
