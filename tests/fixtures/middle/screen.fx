texture SceneCopy : OFFSCREENRENDERTARGET;
float4 VS(float4 p : POSITION0) : POSITION0 { return p; }
float4 PS() : COLOR0 { return float4(0.0, 0.0, 0.0, 1.0); }

technique Screen < string Script = "ScriptExternal=Color; Pass=Main;"; > {
    pass Main < string Script = "Draw=Buffer;"; > {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
